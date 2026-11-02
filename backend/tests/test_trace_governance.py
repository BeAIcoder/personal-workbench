"""工具轨迹（chat_messages.trace）落库大小治理回归。"""
import json

from sqlalchemy import select

from app.agents.orchestrator import _govern_trace, _persist
from app.database import Base, SessionLocal, engine
from app.models import ChatMessage


def test_govern_trace_small_passes_through():
    small = [{"tool": "vault_query", "args": {"sql": "SELECT 1"}, "result": "ok"}]
    assert _govern_trace(small) == small


def test_govern_trace_truncates_long_result():
    item = {"tool": "t", "args": {}, "result": "x" * 5000}
    out = _govern_trace([item])
    assert len(out) == 1
    assert out[0]["result"].startswith("x" * 10)
    assert "截断" in out[0]["result"] and "5000" in out[0]["result"]


def test_govern_trace_drops_oldest_on_overflow():
    big = "y" * 3000
    trace = [{"tool": f"t{i}", "args": {}, "result": big} for i in range(40)]
    out = _govern_trace(trace)
    assert out[0]["tool"] == "trace" and "省略" in out[0]["result"]
    newest = out[-1]
    assert newest["tool"] == "t39"
    for it in out[1:]:
        assert len(it["result"]) <= 4000 + 30


def test_persist_governs_trace():
    Base.metadata.create_all(bind=engine)
    big = "z" * 5000
    trace = [{"tool": "t", "args": {}, "result": big} for _ in range(50)]
    _persist("sess-govern", "assistant", "realestate", "回复", trace)
    with SessionLocal() as db:
        row = db.execute(
            select(ChatMessage).where(ChatMessage.session_id == "sess-govern")
        ).scalars().first()
    assert row is not None
    body = json.dumps(row.trace, ensure_ascii=False)
    assert len(body) < 200_000
    assert "省略" in body or all(len(it["result"]) <= 4000 + 30 for it in row.trace)