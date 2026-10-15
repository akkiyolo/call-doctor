from calldoctor import diagnose as dg
from calldoctor.schemas import Transcript, Turn, TurnLabel

T = Transcript(call_id="c", source="synthetic", turns=[
    Turn(idx=0, speaker="bot", text="Namaste"),
    Turn(idx=1, speaker="customer", text="Nahi chahiye"),
    Turn(idx=2, speaker="bot", text="Ek baar sun lijiye"),
])
CATS = ["pushy_after_refusal", "ignored_question"]


def out(break_idx, cat="pushy_after_refusal", label_cats=("pushy_after_refusal",)):
    return dg._DiagOut(
        labels=[TurnLabel(idx=2, categories=list(label_cats), evidence="Ek baar", severity=2)],
        break_idx=break_idx, break_category=cat, rationale="r",
    )


def test_valid_diagnosis_has_no_problems():
    assert dg._problems(out(2), T, CATS) == []


def test_break_on_customer_turn_is_flagged():
    assert any("not a bot turn" in p for p in dg._problems(out(1), T, CATS))


def test_unknown_categories_are_flagged():
    probs = dg._problems(out(2, cat="rude", label_cats=("rude",)), T, CATS)
    assert len(probs) == 2


def test_semantic_repair_retries_once(monkeypatch, tmp_path):
    replies = iter([out(1), out(2)])
    monkeypatch.setattr(dg.llm, "complete", lambda *a, **k: next(replies))
    monkeypatch.setattr(dg.store, "load_transcript", lambda cid: T)
    monkeypatch.setattr(dg.store, "load", lambda *a: None)
    saved = {}
    monkeypatch.setattr(dg.store, "save", lambda cid, name, obj: saved.setdefault(name, obj))
    monkeypatch.setattr(dg, "category_ids", lambda: CATS)
    d = dg.diagnose("c")
    assert d.break_idx == 2 and saved["diagnosis.json"] == d
