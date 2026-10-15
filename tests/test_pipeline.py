from calldoctor import judge_check as jc
from calldoctor import pipeline


def test_run_all_isolates_failures_and_still_reports(monkeypatch):
    monkeypatch.setattr(pipeline.store, "transcript_ids", lambda: ["a", "b"])
    monkeypatch.setattr(pipeline.diagnose, "diagnose", lambda cid, force=False: None)
    monkeypatch.setattr(pipeline.clone, "clone", lambda cid, force=False: None)

    def one(cid, force=False, n=None, log=print):
        if cid == "a":
            raise RuntimeError("quota")
        return {"call_id": cid, "break": "x", "summary": None}

    monkeypatch.setattr(pipeline, "run_one", one)
    reported = []
    monkeypatch.setattr(pipeline.report, "report", lambda: reported.append(1) or [])
    rows = pipeline.run_all(log=lambda m: None)
    assert [r["status"] for r in rows] == ["failed: RuntimeError", "ok"]
    assert reported == [1]


def test_judge_agreement_math():
    items = [{"agree": {"a": True, "b": False}}, {"agree": {"a": True, "b": True}}]
    assert jc.agreement(items) == 0.75
    assert jc.agreement([]) is None
