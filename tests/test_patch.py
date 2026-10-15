from calldoctor import patch as pt
from calldoctor.schemas import Transcript, Turn

OLD = "## Rules\n- Keep replies short.\n- If the customer says no, try once more.\n"
T = Transcript(call_id="c", source="synthetic", turns=[
    Turn(idx=0, speaker="bot", text="Aapke liye 3 lakh ka offer hai"),
    Turn(idx=1, speaker="customer", text="Mujhe 2 lakh chahiye but abhi nahi, baad mein call karo"),
])


def test_general_rule_passes():
    new = OLD.replace("- If the customer says no, try once more.",
                      "- If the customer clearly refuses, do not pitch again; thank them and end the call.")
    assert pt.overfit_problems(OLD, new, T) == []


def test_copied_transcript_ngram_rejected():
    new = OLD + "- If they say 'abhi nahi, baad mein call karo', offer a callback.\n"
    probs = pt.overfit_problems(OLD, new, T)
    assert any("copies 4+ word" in p for p in probs)


def test_call_specific_number_rejected():
    new = OLD + "- If the customer wants 2 lakh, confirm the amount first.\n"
    assert any("call-specific numbers" in p for p in pt.overfit_problems(OLD, new, T))


def test_too_many_lines_rejected_and_noop_rejected():
    new = OLD + "".join(f"- rule {i}\n" for i in range(9))
    assert any("too large" in p for p in pt.overfit_problems(OLD, new, T))
    assert any("no change" in p for p in pt.overfit_problems(OLD, OLD, T))


def test_normalize_strips_fences_and_keeps_newline():
    assert pt._normalize("<<<\nabc\n>>>", "x\n") == "abc\n"
