import pytest

from app.config import Settings
from app.services.chat import ChatError, ChatService
from app.services.tracing import Tracer


def _settings(**kw) -> Settings:
    return Settings(database_url="postgresql+psycopg://x", **kw)


def test_available_models_puts_default_first_without_duplicates():
    s = _settings(openai_model="gpt-4.1", openai_models=" gpt-5.5, gpt-4.1 ,,gpt-5.4-mini")
    assert s.available_models == ["gpt-4.1", "gpt-5.5", "gpt-5.4-mini"]


def test_model_selection_defaults_and_rejects_unknown():
    s = _settings(openai_model="gpt-5.5", openai_models="gpt-5.5,gpt-4.1")
    chat = ChatService(s, Tracer(s))
    assert chat._model(None) == "gpt-5.5"
    assert chat._model("gpt-4.1") == "gpt-4.1"
    with pytest.raises(ChatError) as e:
        chat._model("gpt-5.5-pro")
    assert e.value.code == "invalid_model"
