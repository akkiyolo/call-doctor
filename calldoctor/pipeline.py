"""Full pipeline for one call or all calls. Used by `run-all` and by the web app's background jobs."""
from __future__ import annotations

from typing import Callable

from . import clone, diagnose, llm, patch, replay, report, score, store

Log = Callable[[str], None]


def run_one(call_id: str, force: bool = False, n: int | None = None, log: Log = print) -> dict:
    """Each step reuses its cache unless force. Raises on the first failing step."""
    log(f"[{call_id}] diagnose")
    d = diagnose.diagnose(call_id, force=force)
    log(f"[{call_id}] break at #{d.break_idx} ({d.break_category}); clone")
    clone.clone(call_id, force=force)
    log(f"[{call_id}] patch")
    patch.patch(call_id, force=force)
    log(f"[{call_id}] replay")
    replay.replay(call_id, n=n, force=force)
    log(f"[{call_id}] score + regression")
    s = score.score(call_id, force=force)
    return {"call_id": call_id, "break": d.break_category, "summary": s}


def run_all(force: bool = False, n: int | None = None, log: Log = print,
            call_ids: list[str] | None = None) -> list[dict]:
    """Diagnose+clone everything first so regression targets exist, then patch/replay/score.

    One call failing (quota, bad JSON) is recorded and the rest continue. Budget exhaustion stops all.
    """
    ids = call_ids or store.transcript_ids()
    rows = []
    for cid in ids:  # pass 1: cheap analysis for every call (regression needs other calls' forks)
        try:
            diagnose.diagnose(cid, force=force)
            clone.clone(cid, force=force)
        except llm.BudgetExceeded:
            raise
        except Exception as e:
            log(f"[{cid}] failed in diagnose/clone: {type(e).__name__}: {str(e)[:200]}")
    for cid in ids:
        try:
            rows.append({**run_one(cid, force=False, n=n, log=log), "status": "ok"})
        except llm.BudgetExceeded:
            report.report()
            raise
        except Exception as e:
            log(f"[{cid}] failed: {type(e).__name__}: {str(e)[:200]}")
            rows.append({"call_id": cid, "status": f"failed: {type(e).__name__}"})
    for p in report.report():
        log(f"wrote {p.name}")
    return rows
