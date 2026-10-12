import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers.api import router
from app.services.chat import ChatService, mark_interrupted_runs
from app.services.pricing import PRICES
from app.services.prompts import PromptError, load_base, load_modes
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
    try:
        load_base()
        log.info("modes=%s", [m.id for m in load_modes()])
    except PromptError:
        log.exception("invalid prompts: messages will fail with config_error until fixed")
    unpriced = [m for m in settings.available_models if m not in PRICES]
    if unpriced:
        log.warning("no price defined for %s: estimated cost and cost limits will not count them", unpriced)
    yield
    tracer.shutdown()


app = FastAPI(title="OpenAI Query App", version="0.1.0", lifespan=lifespan)
app.include_router(router)
