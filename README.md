# Momants Conversation Stats

Monorepo for analyzing conversation sentiment with a FastAPI backend and Vue frontend, orchestrated via Docker Compose.

## Stack

- **Backend**: FastAPI, SQLAlchemy 2, PostgreSQL, Hugging Face Transformers
- **Frontend**: Vue 3 + Vite
- **ML**: Sentiment analysis with on-disk Hugging Face cache (persisted across container restarts)
- **Scheduling**: Plain Python pipeline (ingest → insights → cache warm), no Celery

## Quick start

```bash
cp .env.example .env
# Set SCHEDULED_AGENT_IDS to your agent UUID(s)
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

## Architecture

**Write path** (scheduler container, every 2h by default):

```
cron → python -m app.pipeline run-all → ingest → insights → warm Redis cache
```

**Read path** (API container):

```
Frontend → FastAPI GET → Redis only (503 if cache not warm)
```

Dashboard reads never hit PostgreSQL or run ML. If cache is cold, the UI shows a warning.

## Hugging Face model caching

Models are stored in the `hf_cache` Docker volume (`HF_HOME=/app/.cache/huggingface`). The scheduler preloads models on startup; the API does not.

Set `PRELOAD_MODELS=false` in `.env` to defer model loading until the first pipeline run.

## Local development (without Docker)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://conversation_stats:conversation_stats@localhost:5432/conversation_stats
uvicorn app.main:app --reload
```

### Pipeline (manual)

```bash
export APP_ROLE=scheduler PRELOAD_MODELS=true
python -m app.pipeline run --agent-id=<uuid>
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Services (Docker Compose)

| Service | Role |
|---------|------|
| `api` | FastAPI HTTP (cache-only reads, no ML preload) |
| `scheduler` | Cron-driven pipeline (ingest + insights + cache warm) |
| `redis` | Response cache + pipeline locks |
| `db` | PostgreSQL (durable precomputed data) |
| `frontend` | Vue dashboard |

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (DB + Redis) |
| GET | `/api/health/status` | Pipeline status + last job timestamps |
| GET | `/api/health/scheduler` | Scheduler heartbeat + cache readiness |
| POST | `/api/pipeline/run` | Manual full pipeline run (background) |
| GET | `/api/conversations` | List conversations (cache-only, requires `agent_id`) |
| GET | `/api/conversations/{id}/timeline` | Sentiment arc, response gaps, unanswered flags |
| GET | `/api/ingest/latest` | Latest ingest job for an agent |
| GET | `/api/insights/jobs/latest` | Latest insights job for an agent |
| GET | `/api/insights/overview` | Agent rollup stats (cache-only) |
| GET | `/api/insights/questions` | Top question clusters (cache-only) |
| GET | `/api/insights/unanswered` | Unanswered question list (cache-only) |
| DELETE | `/api/insights?agent_id=` | Remove insights only |
| DELETE | `/api/agents/{agent_id}` | Delete **everything** for an agent |

## Logs

```bash
# Scheduler — ingest + insights ML (best place to debug pipeline)
docker compose logs -f scheduler

# API — HTTP requests
docker compose logs -f api
```

### Run database migrations

```bash
docker compose exec api alembic upgrade head
```
