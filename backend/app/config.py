from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_role: str = "api"  # api | scheduler

    database_url: str = "postgresql+psycopg://conversation_stats:conversation_stats@localhost:5432/conversation_stats"
    # SQLAlchemy engine connection pool. Must be large enough for the per-process peak of
    # concurrent sessions (per-agent ingest workers + insights + main + cache warming).
    db_pool_size: int = 15
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 30.0
    backend_cors_origins: str = "http://localhost:5173"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_url: str = "redis://localhost:6379/2"
    redis_pubsub_url: str = "redis://localhost:6379/3"
    cache_ttl_seconds: int = 10800

    # Scheduler
    pipeline_cron: str = "0 */2 * * *"
    pipeline_interval_seconds: int = 7200
    scheduled_agent_ids: str = ""
    cache_enabled: bool = True
    response_sla_seconds: int = 60

    # Hugging Face
    sentiment_model: str = "tabularisai/multilingual-sentiment-analysis"
    # Stage 2 (dual sentiment) models. Polarity + emotion run on English text, so a
    # language-detection + translation step precedes them (see model_registry / sentiment_service).
    polarity_model: str = "cardiffnlp/twitter-roberta-base-sentiment-latest"
    emotion_model: str = "SamLowe/roberta-base-go_emotions"
    emotion_top_k: int = 3
    # Where the stage-2 polarity/emotion models run: local (CPU in-container) | hf_api
    # (HF serverless router) | hf_endpoint (dedicated Inference Endpoint URLs below).
    # Uses HF_TOKEN + the shared hf_inference_* throughput settings.
    sentiment_inference_mode: str = "local"
    polarity_inference_endpoint: str = ""
    emotion_inference_endpoint: str = ""
    # Google Cloud Translation API v2 (Basic). Translation is skipped entirely when the
    # detected language is English or the key is empty.
    google_translate_api_key: str = ""
    translation_cache_ttl_seconds: int = 86400
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    intent_model: str = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    intent_inference_mode: str = "local"  # local | hf_api | hf_endpoint
    hf_token: str = ""
    intent_inference_endpoint: str = ""
    # Remote HF inference throughput: per-text NLI calls are dispatched concurrently up to
    # this many in flight (bounded process-wide), instead of being serialized one at a time.
    hf_inference_concurrency: int = 12
    hf_inference_max_retries: int = 5
    hf_inference_retry_backoff_seconds: float = 2.0
    hf_inference_timeout_seconds: float = 120.0
    hf_home: str = "/app/.cache/huggingface"
    preload_models: bool = True
    sentiment_batch_size: int = 16
    sentiment_confidence_threshold: float = 0.55
    question_cluster_distance: float = 0.25
    # Clusters smaller than this are dropped from the FAQ as noise (one-off questions).
    question_min_cluster_size: int = 2
    intent_labels: str = "refund,shipping,order_status,account,pricing,product_info,complaint,technical_support,general"
    intent_confidence_threshold: float = 0.35
    intent_complaint_min_score: float = 0.45
    intent_supported_languages: str = "nl,en,de,fr,es"
    intent_hypothesis_template: str = ""
    unanswered_similarity_threshold: float = 0.45
    unanswered_nli_threshold: float = 0.55
    unanswered_nli_labels: str = "answers the question,partially answers,does not answer,deflects"

    # Gotenberg (Chromium HTML->PDF) service for exporting the client report.
    gotenberg_url: str = "http://gotenberg:3000"
    gotenberg_timeout_seconds: float = 120.0
    # Directory where pipeline-generated rapport PDFs are stored for preview/download.
    report_exports_dir: str = "/app/data/reports"

    # Slack notifications: DM the user who requested a rapport run at each milestone.
    # Requires a bot token (xoxb-...) with scopes: users:read.email, chat:write, im:write.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_timeout_seconds: float = 10.0
    # Public base URL of the rapport dashboard, used to build the "PDF ready" link.
    app_base_url: str = "http://localhost:5173"

    auth_required: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:5173/oauth/callback"
    auth_secret: str = "change-me-in-production"
    auth_token_ttl_seconds: int = 604800
    auth_allowed_email_domains: str = "momants.ai"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"

    # Momants API
    momants_api_base_url: str = ""
    momants_dev_identifier: str = ""
    momants_dev_password: str = ""
    momants_api_key: str = ""
    momants_api_connect_timeout_seconds: float = 30.0
    momants_api_read_timeout_seconds: float = 120.0
    momants_api_max_retries: int = 6
    momants_api_retry_backoff_seconds: float = 2.0
    # Global client-side rate cap shared across all ingest threads, to stay under the Momants
    # API limit and avoid 429s. <= 0 disables throttling.
    momants_api_max_requests_per_second: float = 5.0
    ingestion_max_conversations: int = 5000
    ingestion_batch_size: int = 100
    # The Momants API caps results at 50/page but computes total_pages from the requested size,
    # so requesting >50 makes it report half the pages and pagination silently drops half the data.
    ingestion_page_size: int = 50
    ingestion_fetch_delay_seconds: float = 0.5
    # Date-range ("weekly window") ingestion: sweep the inbox backwards in fixed windows
    # instead of paging from page 1 (which is capped and can't reach all conversations).
    ingestion_use_date_windows: bool = True
    ingestion_window_days: int = 1
    # On a full (no-watermark) run, stop sweeping after this many consecutive empty windows.
    ingestion_max_empty_windows: int = 4
    # When False (default), ingest runs in parallel batches and insights run as ONE consolidated
    # pass over all newly-imported conversations afterwards — far better inference throughput than
    # 25 small per-batch insights jobs. Set True only to recover the legacy per-batch behaviour.
    pipeline_insights_per_batch: bool = False
    max_concurrent_jobs: int = 3
    max_concurrent_ingest: int = 3
    max_concurrent_insights: int = 2
    max_concurrent_sentiment: int = 2
    # Per-agent caps so a single agent can't take every global slot (fairness for parallel
    # agent runs). The effective limit is min(global, per-agent).
    max_concurrent_ingest_per_agent: int = 8
    max_concurrent_insights_per_agent: int = 4
    max_concurrent_sentiment_per_agent: int = 4
    # Texts handed to a single zero-shot call; they are now classified concurrently, so a
    # larger batch keeps the HF inference pool well-fed.
    intent_batch_size: int = 32

    @property
    def auth_enabled(self) -> bool:
        return self.auth_required and bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token.strip())

    @property
    def slack_commands_enabled(self) -> bool:
        return bool(self.slack_bot_token.strip() and self.slack_signing_secret.strip())

    @property
    def auth_allowed_email_domain_list(self) -> list[str]:
        return [domain.strip().lower() for domain in self.auth_allowed_email_domains.split(",") if domain.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def scheduled_agent_id_list(self) -> list[str]:
        return [agent_id.strip() for agent_id in self.scheduled_agent_ids.split(",") if agent_id.strip()]

    @property
    def intent_uses_remote_inference(self) -> bool:
        return self.intent_inference_mode in ("hf_api", "hf_endpoint")

    @property
    def sentiment_uses_remote_inference(self) -> bool:
        return self.sentiment_inference_mode in ("hf_api", "hf_endpoint")

    @property
    def intent_label_list(self) -> list[str]:
        return [label.strip() for label in self.intent_labels.split(",") if label.strip()]

    @property
    def intent_supported_language_list(self) -> list[str]:
        return [lang.strip().lower() for lang in self.intent_supported_languages.split(",") if lang.strip()]

    @property
    def unanswered_nli_label_list(self) -> list[str]:
        return [label.strip() for label in self.unanswered_nli_labels.split(",") if label.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
