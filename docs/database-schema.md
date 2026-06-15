# Database schema — stored data points

All persisted data lives in **8 PostgreSQL tables**. This document lists every field currently stored, grouped by table and layer.

---

## Overview

| Layer | Tables | Fields |
|---|---|---|
| Raw ingested data | `conversations`, `messages` | 18 |
| ML analysis | `sentiment_analyses` | 10 |
| Job tracking | `ingestion_jobs`, `insights_jobs` | 22 |
| Computed insights | `conversation_metrics`, `unanswered_questions`, `question_clusters` | 44 |

**Total: 8 tables, 94 mapped columns** (+1 unused `llm_fallback_count` column in DB from migration 001, not mapped in code).

---

## 1. `conversations`

Ingested from Momants.

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `external_id` | string (UUID) | Momants conversation ID |
| `agent_id` | string (UUID) | Momants agent ID |
| `title` | string | Display title (usually member name) |
| `member_name` | string | Customer/member name |
| `integration_type` | string | Messaging channel (e.g. WhatsApp) |
| `resolved` | bool | Whether conversation is resolved |
| `takeover` | bool | Whether a human took over |
| `source_last_seen` | datetime | Last activity timestamp from Momants |
| `created_at` | datetime | When row was first saved locally |

---

## 2. `messages`

Ingested from Momants.

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `external_id` | string (UUID) | Momants message ID |
| `conversation_id` | int (FK) | Parent conversation |
| `role` | string | `"member"` or `"agent"` |
| `from_agent` | bool | Whether sent by the agent |
| `content` | text | Normalized message text |
| `source_created_at` | datetime | Original Momants message timestamp |
| `created_at` | datetime | When row was first saved locally |

**Note:** `source_created_at` is the authoritative message date. If missing or unparseable, downstream code falls back to `created_at`.

---

## 3. `sentiment_analyses`

ML sentiment result per member message (1:1 with message).

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `message_id` | int (FK, unique) | Linked message |
| `stars` | int (1–5) | Sentiment star rating |
| `label` | string | Sentiment label (e.g. POSITIVE, NEGATIVE) |
| `score` | float | Confidence score |
| `model_name` | string | Model used for analysis |
| `raw_label` | string | Raw model output label |
| `raw_score` | float | Raw model output score |
| `low_confidence` | bool | Flag when confidence is low |
| `analyzed_at` | datetime | When analysis ran |

---

## 4. `ingestion_jobs`

Tracks bulk conversation import jobs.

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `agent_id` | string (UUID) | Agent being ingested |
| `limit` | int | Max conversations to fetch |
| `reanalyze` | bool | Re-run sentiment on existing messages |
| `status` | string | `pending`, `running`, `complete`, `failed`, `cancelled` |
| `processed` | int | Conversations processed |
| `failed` | int | Conversations that failed |
| `messages_analyzed` | int | Messages with new sentiment analysis |
| `error` | text | Error/cancel message |
| `created_at` | datetime | Job start |
| `completed_at` | datetime | Job finish |

**Legacy column (DB only):** `llm_fallback_count` — created by migration 001, not mapped in SQLAlchemy models.

---

## 5. `insights_jobs`

Tracks insights pipeline runs (metrics, unanswered questions, clustering).

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `agent_id` | string (UUID) | Agent being analyzed |
| `status` | string | Job status |
| `phase` | string | Current phase (`metrics`, etc.) |
| `processed` | int | Items processed |
| `limit` | int | Optional cap |
| `failed` | int | Failures |
| `messages_analyzed` | int | Messages analyzed |
| `error` | text | Error message |
| `phase_detail` | text | Human-readable phase description |
| `phase_progress` | int | Progress within current phase |
| `phase_total` | int | Total items in current phase |
| `created_at` | datetime | Job start |
| `completed_at` | datetime | Job finish |

---

## 6. `conversation_metrics`

Computed per-conversation analytics (1:1 with conversation).

### Keys & meta

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `conversation_id` | int (FK, unique) | Linked conversation |
| `agent_id` | string (UUID) | Agent ID |
| `computed_at` | datetime | When metrics were computed |

### Sentiment arc

| Field | Type | Description |
|---|---|---|
| `start_stars` | int | First member message stars |
| `end_stars` | int | Last member message stars |
| `delta_stars` | int | Change start → end |
| `avg_stars` | float | Average stars across member messages |
| `low_point_stars` | int | Minimum stars |
| `high_point_stars` | int | Maximum stars |
| `trajectory` | string | e.g. `improving`, `declining`, `mixed` |
| `timeline_json` | text (JSON) | Per-message sentiment timeline |

### Response times

| Field | Type | Description |
|---|---|---|
| `first_response_seconds` | float | Time to first agent reply |
| `median_response_seconds` | float | Median agent response time |
| `max_response_seconds` | float | Longest agent response time |
| `unanswered_member_count` | int | Member messages without a reply |

### Depth

| Field | Type | Description |
|---|---|---|
| `total_messages` | int | Total message count |
| `member_messages` | int | Member message count |
| `agent_messages` | int | Agent message count |
| `depth_ratio` | float | Agent/member message ratio |
| `depth_bucket` | string | e.g. `shallow`, `medium`, `deep` |

### Intent

| Field | Type | Description |
|---|---|---|
| `intent_label` | string | Zero-shot intent classification |
| `intent_score` | float | Intent confidence |

### Unanswered questions (aggregates)

| Field | Type | Description |
|---|---|---|
| `unanswered_question_count` | int | Total unanswered questions |
| `unanswered_no_reply_count` | int | Questions with no reply at all |
| `unanswered_weak_answer_count` | int | Questions with weak answers |
| `unanswered_semantic_count` | int | Semantically unanswered |
| `last_unanswered_question_text` | text | Most recent unanswered question |

---

## 7. `unanswered_questions`

Individual unanswered or weakly-answered member questions.

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `message_id` | int (FK, unique) | The question message |
| `conversation_id` | int (FK) | Parent conversation |
| `agent_id` | string (UUID) | Agent ID |
| `question_text` | text | The question content |
| `agent_reply_message_id` | int (FK) | Agent's reply message, if any |
| `agent_reply_text` | text | Agent's reply content |
| `status` | string | `no_reply`, `weak_answer`, `not_answered`, `answered` |
| `similarity_score` | float | Semantic similarity question ↔ reply |
| `nli_label` | string | NLI classification label |
| `nli_score` | float | NLI confidence |
| `intent_label` | string | Intent classification |
| `computed_at` | datetime | When computed |

---

## 8. `question_clusters`

Grouped similar unanswered questions from an insights run.

| Field | Type | Description |
|---|---|---|
| `id` | int (PK) | Internal primary key |
| `insights_job_id` | int (FK) | Parent insights job |
| `agent_id` | string (UUID) | Agent ID |
| `rank` | int | Rank by frequency (1 = most common) |
| `count` | int | Number of questions in cluster |
| `representative_text` | text | Centroid/representative question |
| `examples_json` | text (JSON) | Up to 3 example questions |
| `intent_label` | string | Intent classification |
| `intent_score` | float | Intent confidence |

---

## JSON blobs

### `timeline_json` (in `conversation_metrics`)

Array of objects, one per member message:

```json
{
  "index": 0,
  "message_id": 123,
  "timestamp": "2026-06-10T12:00:00+00:00",
  "stars": 4,
  "label": "POSITIVE",
  "content_preview": "first 120 chars of message..."
}
```

### `examples_json` (in `question_clusters`)

Array of up to 3 example questions:

```json
{
  "text": "Where is my order?",
  "conversation_id": 42
}
```

---

## Entity relationships

```
conversations
  ├── messages
  │     └── sentiment_analyses (1:1)
  ├── conversation_metrics (1:1)
  └── unanswered_questions

insights_jobs
  └── question_clusters

ingestion_jobs (standalone)
insights_jobs (standalone)
```
