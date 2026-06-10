# Momants Conversation Stats

Monorepo for analyzing conversation sentiment with a FastAPI backend and Vue frontend, orchestrated via Docker Compose.

## Stack

- **Backend**: FastAPI, SQLAlchemy 2, PostgreSQL, Hugging Face Transformers
- **Frontend**: Vue 3 + Vite
- **ML**: Sentiment analysis with on-disk Hugging Face cache (persisted across container restarts)

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

## Hugging Face model caching

Models are stored in the `hf_cache` Docker volume (`HF_HOME=/app/.cache/huggingface`). On first startup the sentiment model is downloaded once; subsequent restarts reuse the cached weights.

Set `PRELOAD_MODELS=false` in `.env` to defer model loading until the first analysis request.

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

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/conversations` | List conversations |
| POST | `/api/conversations` | Create conversation with messages |
| GET | `/api/conversations/{id}` | Get conversation detail |
| POST | `/api/conversations/{id}/messages` | Add message and analyze sentiment |
| GET | `/api/conversations/{id}/stats` | Aggregated sentiment stats |
