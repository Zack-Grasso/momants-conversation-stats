import logging
import sys
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.config import get_settings
from app.database import init_db
from app.ml.model_registry import get_model_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    init_db()
    if settings.preload_models and settings.app_role == "scheduler":
        logger.info("Preloading Hugging Face models into cache")
        get_model_registry().preload()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Momants Conversation Stats", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
