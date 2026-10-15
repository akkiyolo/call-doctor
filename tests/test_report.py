import json

import pytest

from calldoctor import report as rp
from calldoctor.config import get_settings
from calldoctor.schemas import Diagnosis, Patch, ReplayRun, Score, Transcript, Turn, TurnLabel
from calldoctor.score import run_id


@pytest.fixture
def fixture_dirs(tmp_path, monkeypatch):
    s = get_settings()
    for name in ("transcripts_dir", "runs_dir", "reports_dir"):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(s, name, tmp_path / name)
    t = Transcript(call_id="fx", source="synthetic", turns=[
        Turn(idx=0, speaker="bot", text="Namaste ji"),
        Turn(idx=1, speaker="customer", text="Nahi chahiye <script>x</script>"),
        Turn(idx=2, speaker="bot", text="Sir ek baar sun lijiye ₹3,00,000"),
    ])
    (tmp_path / "transcripts_dir" / "fx.json").write_text(t.model_dump_json(), encoding="utf-8")
    run = tmp_path / "runs_dir" / "fx"
    run.mkdir()
    d = Diagnosis(call_id="fx", labels=[TurnLabel(idx=2, categories=["pushy_after_refusal"],
                  evidence="ek baar", severity=3)], break_idx=2, break_category="pushy_after_refusal",
                  rationale="Pushed after a refusal.")
    (run / "diagnosis.json").write_text(d.model_dump_json(), encoding="utf-8")
    (run / "patch.json").write_text(Patch(call_id="fx", target_category="pushy_after_refusal",
        diff="--- a\n+++ b\n@@ -1 +1 @@\n-old rule\n+new rule\n", patched_prompt="p",
        rationale="General rule.").model_dump_json(), encoding="utf-8")
    runs = [ReplayRun(call_id="fx", arm=a, seed=42, turns=[Turn(idx=2, speaker="bot", text=f"{a} reply")],
                      end_reason="customer:refused") for a in ("original", "patched")]
    (run / "replays.json").write_text(json.dumps([r.model_dump() for r in runs]), encoding="utf-8")
    scores = [Score(run_id=run_id(r), reasons="because", next_step_reached=r.arm == "patched", recovery=1,
                    human_likeness=2, compliance_pass=r.arm == "patched",
                    compliance_issues=[] if r.arm == "patched" else ["2: pushed"]) for r in runs]
    (run / "scores.json").write_text(json.dumps([x.model_dump() for x in scores]), encoding="utf-8")
    summary = {"call_id": "fx", "paired_win_rate": 1.0, "n_pairs": 1, "regression_delta": None, "regression": {},
               "original": {"n": 1, "next_step_rate": 0.0, "mean_recovery": 1, "mean_human_likeness": 2,
                            "compliance_pass_rate": 0.0, "mean_composite": -2},
               "patched": {"n": 1, "next_step_rate": 1.0, "mean_recovery": 1, "mean_human_likeness": 2,
                           "compliance_pass_rate": 1.0, "mean_composite": 5}}
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return tmp_path


def test_report_renders_fixture(fixture_dirs):
    paths = rp.report()
    html = (fixture_dirs / "reports_dir" / "fx.html").read_text(encoding="utf-8")
    index = (fixture_dirs / "reports_dir" / "index.html").read_text(encoding="utf-8")
    assert len(paths) == 2
    assert "BREAK" in html and "pushy_after_refusal" in html
    assert 'class="add">+new rule' in html and 'class="del">-old rule' in html
    assert "original reply" in html and "patched reply" in html and "₹3,00,000" in html
    assert "&lt;script&gt;" in html and "<script>x</script>" not in html  # transcript text is escaped
    assert 'href="fx.html"' in index and "100%" in index
    assert "http://" not in html and "https://" not in html  # offline: no external assets


def test_report_renders_undiagnosed_call(fixture_dirs):
    for f in (fixture_dirs / "runs_dir" / "fx").iterdir():
        f.unlink()
    rp.report()
    assert "Not diagnosed yet" in (fixture_dirs / "reports_dir" / "fx.html").read_text(encoding="utf-8")
