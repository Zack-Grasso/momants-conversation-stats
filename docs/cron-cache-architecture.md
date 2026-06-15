# Cron + Cache Architecture (Replace Celery)

Handoff doc for backend: move from on-demand Celery jobs to scheduled preprocessing with a fast, cache-first read path for the frontend.

## Goals

1. **Frontend is always fast** — every page load hits precomputed, cached data. No ML, no Momants API calls, no job orchestration in the request path.
2. **Backend preprocessing is independent** — a scheduled job (cron every 4–6 hours) fetches new data, runs ML/preprocessing, and writes results to the cache the frontend reads.
3. **No Celery** — plain Python scripts with internal parallelism. Simpler to debug, fewer hidden failure modes.

## Current State

### What Celery does today

Celery is glue, not core logic. All heavy work lives in regular Python services:

| Component | Path | Role |
|-----------|------|------|
| Celery app | `backend/app/worker.py` | Broker config, worker startup hook |
| Tasks | `backend/app/tasks.py` | Thin wrappers calling services + retries |
| Ingest trigger | `backend/app/api/ingest.py` | `run_ingestion_job.delay(...)` |
| Insights trigger | `backend/app/api/insights.py` | `run_insights_job.delay(...)` |
| Worker container | `docker-compose.yml` | `celery worker --concurrency=1` |

**Two Celery tasks:**

- `tasks.run_ingestion_job` → `IngestionService.run_job(job_id)` (30 min soft limit, 3 retries)
- `tasks.run_insights_job` → `InsightsService.run_job(job_id)` (60 min soft limit, 3 retries)

Both release per-agent Redis locks in `finally` (`backend/app/locks.py`).

### Data flow today

```
User clicks "Run" (RunPage.vue)
    → POST /api/ingest or POST /api/insights/run
    → Celery worker
        → IngestionService / InsightsService
        → PostgreSQL (conversations, metrics, clusters, etc.)
        → Redis cache invalidate (insights only, on job complete)
    → WebSocket progress via Redis pub/sub

Frontend reads (ResultsPage.vue)
    → GET /api/insights/overview     (Redis-cached)
    → GET /api/insights/questions    (Redis-cached)
    → GET /api/insights/unanswered   (always hits DB)
    → GET /api/conversations/*       (always hits DB)
```

### Redis layout (4 logical DBs on one instance)

| DB | Env var | Purpose |
|----|---------|---------|
| 0 | `REDIS_URL` | Celery broker + job locks/cancel flags |
| 1 | `CELERY_RESULT_BACKEND` | Celery task results (1h expiry) |
| 2 | `REDIS_CACHE_URL` | JSON response cache |
| 3 | `REDIS_PUBSUB_URL` | Job progress pub/sub for WebSockets |

### What is already precomputed

PostgreSQL tables are the durable source of truth for computed data:

- `sentiment_analyses` — per-message sentiment (ingest)
- `conversation_metrics` — tier-1 metrics + intent labels (ingest + insights)
- `question_clusters` — clustered top questions (insights)
- `unanswered_questions` — semantic unanswered detection (insights)
- `ingestion_jobs` / `insights_jobs` — job audit trail

Redis cache (`backend/app/cache.py`) only covers two endpoints today:

- `insights:{agent_id}:overview`
- `insights:{agent_id}:questions`

Default TTL: 300s (`CACHE_TTL_SECONDS`). Cache is invalidated on insights job completion, not proactively warmed.

### ML work (unchanged regardless of Celery)

| Work | When (today) | Service |
|------|--------------|---------|
| Sentiment | Ingest (member messages) | `ingestion_service.py` |
| Tier-1 metrics | Ingest + insights | `metrics_service.py` |
| Question clustering | Insights | `question_analysis_service.py` |
| Intent labeling | Insights | `intent_service.py` |
| Unanswered detection | Insights | `unanswered_question_service.py` |

Models preload on worker (`PRELOAD_MODELS=true`), not on API (`PRELOAD_MODELS=false`).

---

## Target Architecture

```
┌─────────────┐     every 4–6h      ┌──────────────────┐
│   crontab   │ ──────────────────► │  pipeline script │
│ (scheduler  │                     │  (plain Python)  │
│  container) │                     └────────┬─────────┘
└─────────────┘                              │
                                             ▼
                              ┌──────────────────────────┐
                              │ 1. ingest                │
                              │ 2. insights              │
                              │ 3. warm Redis cache      │
                              └────────┬─────────────────┘
                                           ▼
                              ┌──────────────────────────┐
                              │  PostgreSQL + Redis      │
                              └────────┬─────────────────┘
                                           ▼
                              ┌──────────────────────────┐
                              │  FastAPI (read-only path)│ ◄── Frontend
                              └──────────────────────────┘
```

### Design rules

**Frontend / API (read path)**

- Every GET the dashboard uses returns precomputed data.
- Redis first; DB fallback only if cache is cold (should be rare after warm step).
- API container stays lean: no HF model preload, no background tasks.
- No dependency on whether a job is currently running.

**Scheduler (write path)**

- Separate container or host cron runs the pipeline.
- Loads HF models once at startup (`PRELOAD_MODELS=true`).
- Runs ingest → insights sequentially per agent.
- Uses existing Redis locks to prevent overlapping runs.
- At the end: **warm cache** (write all FE payloads to Redis), don't just invalidate.
- Logs to stdout; updates job rows for audit ("last successful run at …").

---

## What to Remove

| Item | Path / location |
|------|-----------------|
| Celery dependency | `backend/requirements.txt` (`celery[redis]`) |
| Celery app | `backend/app/worker.py` |
| Celery tasks | `backend/app/tasks.py` |
| Worker service | `docker-compose.yml` (`worker` service) |
| Celery env vars | `.env.example`, `config.py` (`celery_broker_url`, `celery_result_backend`) |
| Celery health checks | `backend/app/api/health.py` (`celery_app.control.ping()`) |
| Redis DB 0/1 for broker/results | Can collapse to cache + locks only (2 DBs) |

**Optional removals** (if live job progress is no longer needed):

| Item | Path |
|------|------|
| Redis pub/sub | `backend/app/pubsub.py` |
| WebSocket job progress | `backend/app/api/ws.py` |
| Job cancel flags | `backend/app/locks.py` (cancel-related parts) |

---

## What to Keep (unchanged logic)

| Item | Path | Notes |
|------|------|-------|
| `IngestionService` | `backend/app/services/ingestion_service.py` | Core ingest + sentiment |
| `InsightsService` | `backend/app/services/insights_service.py` | Metrics, questions, intents, unanswered |
| All ML services | `backend/app/services/*_service.py` | No rewrite needed |
| Redis cache | `backend/app/cache.py` | Extend coverage + warm at end of pipeline |
| Redis locks | `backend/app/locks.py` | Prevent overlapping cron runs per agent |
| Job tables | `ingestion_jobs`, `insights_jobs` | Audit trail + "last run" for health/status |
| HF disk cache | `hf_cache` Docker volume | Model weights persist across restarts |

---

## What to Build

### 1. Pipeline CLI

New entry point, e.g. `backend/app/pipeline.py` or `backend/scripts/run_pipeline.py`:

```bash
# Run full pipeline for one agent
python -m app.pipeline ingest --agent-id=<uuid>
python -m app.pipeline insights --agent-id=<uuid>

# Or combined
python -m app.pipeline run --agent-id=<uuid>
```

Implementation sketch:

```python
def run_pipeline(agent_id: str) -> None:
    db = SessionLocal()
    try:
        if not acquire_agent_job_lock(agent_id, "pipeline"):
            logger.warning("Pipeline already running for %s, skipping", agent_id)
            return

        # Ingest
        ingest_job = IngestionService(db).create_job(agent_id, limit=..., reanalyze=False)
        IngestionService(db).run_job(ingest_job.id)

        # Insights
        insights_job = InsightsService(db).create_job(agent_id)
        InsightsService(db).run_job(insights_job.id)

        # Warm cache
        warm_agent_cache(db, agent_id)
    finally:
        release_agent_job_lock(agent_id, "pipeline")
        db.close()
```

Reuse retry logic from `tasks.py` inline (3 attempts, 30s / 2m / 8m backoff) instead of Celery's `self.retry()`.

### 2. Cache warming

New function, e.g. in `backend/app/cache.py` or `backend/app/services/cache_warmer.py`:

After pipeline completes, proactively write all frontend payloads:

| Cache key | Source method | Used by |
|-----------|---------------|---------|
| `insights:{agent_id}:overview` | `InsightsService.get_overview()` | ResultsPage |
| `insights:{agent_id}:questions` | `InsightsService.get_questions()` | ResultsPage |
| `insights:{agent_id}:unanswered` | `InsightsService.get_unanswered()` | ResultsPage |
| `conversations:{agent_id}:list` | `ConversationService.list_conversations()` | ResultsPage |

Set TTL to match cron interval (e.g. 6h = 21600s) or longer — cron refreshes before expiry.

Update read endpoints to check cache first (overview/questions already do; extend to unanswered + conversations).

Change `InsightsService.run_job()` completion: call `warm_agent_cache()` instead of only `cache_delete_prefix()`.

### 3. Scheduler container

Replace `worker` in `docker-compose.yml` with something like:

```yaml
scheduler:
  build:
    context: ./backend
  command: supercronic /app/crontab   # or a simple loop + sleep
  environment:
    APP_ROLE: scheduler
    PRELOAD_MODELS: "true"
  volumes:
    - hf_cache:/app/.cache/huggingface
```

Example crontab (every 6 hours):

```
0 */6 * * * python -m app.pipeline run --agent-id=${AGENT_ID}
```

Or use env `SCHEDULED_AGENT_IDS` to loop multiple agents in one cron entry.

### 4. API changes

**Remove or demote job triggers:**

- `POST /api/ingest` — remove from frontend path, or keep as optional manual refresh (spawn pipeline subprocess, not Celery)
- `POST /api/insights/run` — same

**Health endpoint:**

Replace Celery worker ping with:

- Last successful ingest/insights job timestamp from DB
- Optional scheduler heartbeat (Redis key set by cron on success)

**Keep read endpoints unchanged** from the frontend's perspective — same URLs, same response shapes, just always cached.

### 5. Config additions

In `.env.example` / `config.py`:

```env
# Scheduler
PIPELINE_CRON=0 */6 * * *
SCHEDULED_AGENT_IDS=uuid1,uuid2
CACHE_TTL_SECONDS=21600   # match cron interval

# Remove
# CELERY_BROKER_URL
# CELERY_RESULT_BACKEND
```

---

## Parallelism Without Celery

Celery's `--concurrency=1` was intentional (one heavy ML job at a time). Keep that at the agent level via Redis locks.

Within a single pipeline run, parallelize where safe:

| Phase | Parallelism strategy |
|-------|---------------------|
| Fetch conversations | Sequential (Momants API rate limits) |
| Sentiment analysis | `ProcessPoolExecutor` or batched inference per conversation |
| Metrics computation | Batch DB reads, vectorized where possible |
| Question clustering | Already batch-oriented (embeddings) |
| Intent / unanswered | Batch inference |

One process, one log stream, one exit code — much easier to debug than Celery worker restarts or stale tasks in the broker.

---

## Frontend Impact (for context)

Minimal if read endpoints stay the same:

| Today | After |
|-------|-------|
| `RunPage.vue` — start ingest/insights, WebSocket progress | Show "Last updated: …" from job timestamps; optional manual refresh button |
| `useJobRunner.js` — live job tracking | Replace with polling `GET /api/ingest/latest` or health status |
| `ResultsPage.vue` — parallel GETs | Unchanged (benefits from full cache coverage) |

Frontend team can update UX separately; backend should expose `completed_at` on latest jobs via existing endpoints.

---

## Migration Plan

Suggested order — each step is independently deployable:

1. **Add pipeline CLI** calling `IngestionService.run_job()` / `InsightsService.run_job()` directly. Verify it works via `docker compose exec`.
2. **Add cache warming** for all frontend endpoints. Extend TTL.
3. **Add scheduler container** with cron. Run alongside existing Celery worker initially.
4. **Switch frontend** from "Start job" to read-only + "last updated" display.
5. **Remove Celery** — worker container, tasks, broker config, pub/sub (if unused).
6. **Simplify Redis** — drop broker/result DBs; keep cache + locks.

---

## Trade-offs

| Gain | Cost |
|------|------|
| Simpler ops (no worker/broker) | Data is up to 4–6h stale |
| Easier debugging (one process, one log) | No live progress bar during runs |
| Fewer hidden Celery failure modes | Failed runs wait for next cron (unless manual retry added) |
| API stays fast and stateless | Long jobs block the cron slot for that agent (same as today with concurrency=1) |

### Retries

Port from `tasks.py`:

- 3 retries with backoff: 30s, 2m, 8m
- Mark job `failed` in DB with error message
- Insights timeout: 60 min soft limit (keep same guard)

### Optional: manual refresh

If needed later, `POST /api/pipeline/run` can spawn the same CLI via `subprocess` — still no Celery, same code path as cron.

---

## Files Reference

### Modify

| File | Change |
|------|--------|
| `docker-compose.yml` | Replace `worker` with `scheduler` |
| `backend/requirements.txt` | Remove `celery[redis]` |
| `backend/app/config.py` | Remove Celery settings; add scheduler config |
| `.env.example` | Update Redis/Celery section |
| `backend/app/api/ingest.py` | Remove `.delay()` or demote endpoint |
| `backend/app/api/insights.py` | Remove `.delay()`; extend cache on `/unanswered` |
| `backend/app/api/conversations.py` | Add cache layer on list/timeline if needed |
| `backend/app/api/health.py` | Replace Celery ping with last-job timestamps |
| `backend/app/services/insights_service.py` | Warm cache on completion instead of only invalidate |
| `backend/app/main.py` | Model preload for `APP_ROLE=scheduler` |

### Delete (after migration)

| File |
|------|
| `backend/app/worker.py` |
| `backend/app/tasks.py` |

### Create

| File | Purpose |
|------|---------|
| `backend/app/pipeline.py` | CLI entry point for cron |
| `backend/crontab` or scheduler config | Cron schedule |
| `backend/app/services/cache_warmer.py` (optional) | Centralized cache warming logic |

---

## Open Questions

1. **Cron interval** — 4h or 6h? Should `CACHE_TTL_SECONDS` match exactly?
2. **Agent list** — single `AGENT_ID` env var or comma-separated `SCHEDULED_AGENT_IDS`?
3. **Manual refresh** — needed at all, or cron-only?
4. **WebSocket / pub/sub** — remove entirely or keep for optional manual runs?
5. **Sync sentiment on manual conversation create** (`POST /api/conversations`) — keep in API or move to pipeline?
