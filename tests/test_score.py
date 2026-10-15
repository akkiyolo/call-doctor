import re

from calldoctor import prompts, score as sc
from calldoctor.schemas import ReplayRun, Score, Turn

H = [Turn(idx=0, speaker="bot", text="Namaste"), Turn(idx=1, speaker="customer", text="Nahi chahiye")]


def _run(arm, seed=42):
    return ReplayRun(call_id="call_x", arm=arm, seed=seed,
                     turns=[Turn(idx=2, speaker="bot", text="Theek hai, dhanyavaad")], end_reason="x")


def _leaks(text):
    return [w for w in sc.ARM_WORDS if re.search(rf"\b{w}\b", text, re.IGNORECASE)]


def test_judge_input_has_no_arm_labels():
    for arm in ("original", "patched"):
        text = sc.judge_input(H, _run(arm).turns)
        assert _leaks(text) == []
        assert "call_x" not in text and "42" not in text


def test_judge_system_prompt_has_no_arm_labels():
    assert _leaks(prompts.render("judge.md")) == []


def test_run_ids_are_opaque_and_distinct_per_arm():
    a, b = sc.run_id(_run("original")), sc.run_id(_run("patched"))
    assert a != b and not _leaks(a + b)


def test_judge_sees_identical_input_for_both_arms_when_text_matches(monkeypatch):
    seen = []
    monkeypatch.setattr(sc.llm, "complete", lambda role, system, msgs, **k: seen.append(msgs[0]["text"])
                        or sc._JudgeOut(reasons="r", next_step_reached=False, recovery=1,
                                        human_likeness=1, compliance_pass=True, compliance_issues=[]))
    sc.score_runs(H, [_run("original"), _run("patched")], {})
    assert len(seen) == 2 and seen[0] == seen[1]


def _s(ns, rec, comp=True):
    return Score(run_id="r", reasons="", next_step_reached=ns, recovery=rec, human_likeness=1,
                 compliance_pass=comp, compliance_issues=[])


def test_paired_win_rate_counts_ties_half():
    pairs = [(_s(False, 0), _s(True, 2)), (_s(False, 1), _s(False, 1)), (_s(True, 2), _s(True, 2, comp=False))]
    assert sc.paired_win_rate(pairs) == (1 + 0.5 + 0) / 3
