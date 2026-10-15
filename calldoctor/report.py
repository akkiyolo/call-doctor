"""Step 7: render per-call reports and the index as static HTML (also reused by the web app)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import store
from .config import ROOT, get_settings
from .schemas import Diagnosis, Patch, Persona, ReplayRun, Score
from .score import composite, run_id

_env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))


@dataclass
class Urls:
    home: str
    call: Callable[[str], str]
    audio: Callable[[str], str]


STATIC_URLS = Urls(home="index.html", call=lambda c: f"{c}.html", audio=lambda f: f"audio/{f}")


# ---------- shared data ----------

def total_cost() -> float:
    p = get_settings().runs_dir / "llm_log.jsonl"
    if not p.exists():
        return 0.0
    return sum(json.loads(l).get("cost_usd", 0) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())


def judge_agreement() -> dict | None:
    p = get_settings().runs_dir / "judge_check.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    marks = [v for item in data.get("items", []) for v in item.get("agree", {}).values()]
    return {"agreement": sum(marks) / len(marks), "n": len(data.get("items", []))} if marks else None


def diff_lines(diff: str) -> list[tuple[str, str]]:
    out = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            out.append(("meta", line))
        elif line.startswith("@@"):
            out.append(("hunk", line))
        elif line.startswith("+"):
            out.append(("add", line))
        elif line.startswith("-"):
            out.append(("del", line))
        else:
            out.append(("ctx", line))
    return out


def _turn_view(turns, break_idx=None, labels=None, mark_after=True) -> list[dict]:
    labels = labels or {}
    view = []
    for t in turns:
        lab = labels.get(t.idx)
        view.append({
            "idx": t.idx, "speaker": t.speaker, "text": t.text,
            "labels": lab.categories if lab else [], "severity": lab.severity if lab else 0,
            "is_break": break_idx is not None and t.idx == break_idx,
            "after": mark_after and break_idx is not None and t.idx > break_idx,
        })
    return view


def _examples(runs: list[ReplayRun], scores: dict[str, Score]) -> list[dict]:
    """Best and worst patched run by judge composite, each beside the original run on the same seed."""
    by = {(r.arm, r.seed): r for r in runs}
    cands = [(composite(scores[run_id(r)]), r) for r in runs
             if r.arm == "patched" and run_id(r) in scores and ("original", r.seed) in by]
    if not cands:
        cands = [(0, r) for r in runs if r.arm == "patched" and ("original", r.seed) in by]
    if not cands:
        return []
    cands.sort(key=lambda x: (x[0], -x[1].seed))
    picks = [("Best patched run", cands[-1][1])]
    if len(cands) > 1:
        picks.append(("Worst patched run", cands[0][1]))
    else:
        picks[0] = ("Before vs after", cands[0][1])
    out = []
    for title, p in picks:
        o = by[("original", p.seed)]
        side = lambda r, label: {"label": label, "run": r, "score": scores.get(run_id(r)),
                                 "turns": _turn_view(r.turns, mark_after=False)}
        out.append({"title": title, "seed": p.seed, "before": side(o, "Before · original"),
                    "after": side(p, "After · patched")})
    return out


def audio_info(call_id: str, urls: Urls) -> dict | None:
    meta = get_settings().reports_dir / "audio" / f"{call_id}.json"
    if not meta.exists():
        return None
    m = json.loads(meta.read_text(encoding="utf-8"))
    return {"seed": m["seed"], "before": urls.audio(m["before"]), "after": urls.audio(m["after"])}


def call_context(call_id: str, urls: Urls = STATIC_URLS) -> dict:
    t = store.load_transcript(call_id)
    d = store.load(call_id, "diagnosis.json", Diagnosis)
    pt = store.load(call_id, "patch.json", Patch)
    persona = store.load(call_id, "persona.json", Persona)
    runs = store.load_list(call_id, "replays.json", ReplayRun) or []
    scores = {s.run_id: s for s in (store.load_list(call_id, "scores.json", Score) or [])}
    summary_p = get_settings().runs_dir / call_id / "summary.json"
    summary = json.loads(summary_p.read_text(encoding="utf-8")) if summary_p.exists() else None
    labels = {l.idx: l for l in d.labels} if d else {}
    return {
        "call_id": call_id, "source": t.source, "n_turns": len(t.turns),
        "turns": _turn_view(t.turns, d.break_idx if d else None, labels),
        "diagnosis": d, "patch": pt, "persona": persona,
        "diff": diff_lines(pt.diff) if pt else [],
        "summary": summary, "examples": _examples(runs, scores),
        "audio": audio_info(call_id, urls),
    }


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def index_context(urls: Urls = STATIC_URLS) -> dict:
    s = get_settings()
    calls = []
    for cid in store.transcript_ids():
        ctx = call_context(cid, urls)
        d = ctx["diagnosis"]
        calls.append({"call_id": cid, "source": ctx["source"], "summary": ctx["summary"],
                      "break_category": d.break_category if d else None,
                      "break_idx": d.break_idx if d else None})
    scored = [c["summary"] for c in calls if c["summary"] and c["summary"]["original"].get("n")]
    keys = ("next_step_rate", "mean_recovery", "mean_human_likeness", "compliance_pass_rate")
    agg = {
        "win_rate": _mean(x["paired_win_rate"] for x in scored),
        **{arm: {k: _mean(x[arm].get(k) for x in scored) for k in keys} for arm in ("original", "patched")},
    }
    extra = []
    if "pro" not in s.models["judge"]:
        extra.append(f"Judge model is {s.models['judge']} (free-tier fallback), not a pro model; "
                     "expect noisier scores.")
    if s.models["judge"] == s.models["customer"]:
        extra.append("The judge and the customer simulator use the same model.")
    if any(x["original"]["n"] < 5 for x in scored):
        extra.append("Some calls have fewer than 5 runs per arm; treat their rates as anecdotes.")
    return {"calls": calls, "n_scored": len(scored), "agg": agg, "judge_check": judge_agreement(),
            "total_cost": total_cost(), "extra_limitations": extra}


def render(template: str, ctx: dict, urls: Urls = STATIC_URLS, app_mode: bool = False) -> str:
    return _env.get_template(template).render(
        **ctx, urls=urls, app_mode=app_mode, generated_at=time.strftime("%Y-%m-%d %H:%M"))


def report(call_ids: list[str] | None = None) -> list[Path]:
    out_dir = get_settings().reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for cid in call_ids or store.transcript_ids():
        p = out_dir / f"{cid}.html"
        p.write_text(render("call.html", call_context(cid)), encoding="utf-8")
        written.append(p)
    p = out_dir / "index.html"
    p.write_text(render("index.html", index_context()), encoding="utf-8")
    written.append(p)
    return written
