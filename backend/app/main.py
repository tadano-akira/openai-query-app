import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers.api import router
from app.services.chat import ChatService, mark_interrupted_runs
from app.services.tracing import Tracer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    tracer = Tracer(settings)
    app.state.tracer = tracer
    app.state.chat = ChatService(settings, tracer)
    try:
        n = await mark_interrupted_runs()
        if n:
            log.warning("marked %d running runs as interrupted", n)
    except Exception:
        log.exception("failed to mark interrupted runs")
    log.info("model=%s langfuse=%s", settings.openai_model, "on" if tracer.enabled else "off")
    yield
    tracer.shutdown()


app = FastAPI(title="OpenAI Query App", version="0.1.0", lifespan=lifespan)
app.include_router(router)
