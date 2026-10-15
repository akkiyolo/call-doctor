import pytest
from pydantic import BaseModel

from calldoctor import llm


class Out(BaseModel):
    x: int


def test_repair_retry_on_validation_failure(monkeypatch):
    replies = iter(['{"x": "not-an-int"}', '{"x": 3}'])
    seen = []

    def fake(role, system, messages, schema, seed):
        seen.append(messages)
        return next(replies)

    monkeypatch.setattr(llm, "_generate", fake)
    out = llm.complete("judge", "sys", [{"role": "user", "text": "hi"}], schema=Out)
    assert out.x == 3
    assert len(seen) == 2 and "failed validation" in seen[1][-1]["text"]


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        llm.complete("nope", "s", [])


def test_pricing_lookup_order():
    assert llm.price_for("gemini-3.5-flash-lite") == (0.10, 0.40)
    assert llm.price_for("gemini-3.5-flash") == (0.50, 3.00)
    assert llm.price_for("gemini-3.1-pro-preview") == (2.00, 12.00)
