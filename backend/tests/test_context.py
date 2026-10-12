import uuid

from app.models import Message, Turn
from app.services.chat import build_context


def _turn(seq: int, user: str, answer: str | None) -> Turn:
    t = Turn(id=uuid.uuid4(), seq=seq)
    t.user_message = Message(id=uuid.uuid4(), role="user", content=user)
    t.adopted_message = Message(id=uuid.uuid4(), role="assistant", content=answer) if answer else None
    return t


def test_build_context_skips_turns_without_success_and_orders_by_seq():
    turns = [_turn(2, "q2", None), _turn(3, "q3", "a3"), _turn(1, "q1", "a1")]
    context, ids = build_context(turns)
    assert [m["content"] for m in context] == ["q1", "a1", "q3", "a3"]
    assert [m["role"] for m in context] == ["user", "assistant", "user", "assistant"]
    assert len(ids) == 4
