import pytest
from pydantic import ValidationError

from calldoctor.schemas import Diagnosis, Persona, Score, Transcript, TurnLabel


def test_transcript_roundtrip_keeps_hinglish_exact():
    raw = {"call_id": "c1", "source": "synthetic", "turns": [
        {"idx": 0, "speaker": "bot", "text": "Namaste, kya main Rahul ji se baat kar rahi hoon?"},
        {"idx": 1, "speaker": "customer", "text": "हाँ बोलिए, kaun?"},
    ]}
    t = Transcript.model_validate(raw)
    assert Transcript.model_validate_json(t.model_dump_json()) == t
    assert t.turns[1].text == "हाँ बोलिए, kaun?"


@pytest.mark.parametrize("bad", [
    {"call_id": "c", "source": "fake", "turns": []},
    {"call_id": "c", "source": "real", "turns": [{"idx": 0, "speaker": "agent", "text": "x"}]},
])
def test_transcript_rejects_bad_enums(bad):
    with pytest.raises(ValidationError):
        Transcript.model_validate(bad)


def test_severity_and_score_ranges():
    with pytest.raises(ValidationError):
        TurnLabel(idx=0, categories=[], evidence="", severity=4)
    with pytest.raises(ValidationError):
        Score(run_id="r", reasons="x", next_step_reached=True, recovery=3,
              human_likeness=1, compliance_pass=True, compliance_issues=[])
    Diagnosis(call_id="c", labels=[], break_idx=2, break_category="x", rationale="y")


def test_persona_mix_bounds():
    base = dict(goals="g", objections=[], patience=1, verbosity=1, interrupts=False,
                trust_level=1, will_convert_if="a", hard_stop_if="b", style_examples=[])
    Persona(hindi_english_mix=0.6, **base)
    with pytest.raises(ValidationError):
        Persona(hindi_english_mix=1.5, **base)
