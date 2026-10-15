"""Web app: upload a call, run the pipeline as a background job, browse the reports.

Run locally:  uvicorn calldoctor.web:app --reload
On Render:    see render.yaml (APP_PASSWORD is required; the URL is public).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import queue
import re
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from . import llm, pipeline, report, store
from .config import get_settings
from .schemas import Transcript, Turn

MAX_UPLOAD = 25 * 1024 * 1024
COOKIE = "cd_session"
URLS = report.Urls(home="/", call=lambda c: f"/calls/{c}", audio=lambda f: f"/audio/{f}")
_ID = re.compile(r"^[\w-]{1,80}$")


def _password() -> str:
    pw = os.environ.get("APP_PASSWORD", "")
    if not pw:
        raise RuntimeError("APP_PASSWORD must be set: this app spends API credits on a public URL")
    return pw


def _token() -> str:
    return hmac.new(_password().encode(), b"call-doctor-session", hashlib.sha256).hexdigest()


# ---------- jobs: one worker, so concurrent uploads can't multiply API spend ----------

@dataclass
class Job:
    id: str
    call_id: str
    kind: str
    status: str = "queued"
    created: float = field(default_factory=time.time)
    log: list[str] = field(default_factory=list)
    n: int | None = None
    persona: str | None = None
    audio_path: str | None = None

    @property
    def started(self) -> str:
        return time.strftime("%d %b %H:%M", time.localtime(self.created))

    def write(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')}  {msg}")


JOBS: dict[str, Job] = {}
_q: "queue.Queue[Job]" = queue.Queue()


def _work(job: Job) -> None:
    job.status = "running"
    try:
        if job.kind == "audio":
            from . import audio

            job.write("transcribing with ElevenLabs Scribe")
            turns, mapping = audio.words_to_turns(audio.transcribe(Path(job.audio_path)))
            job.write(f"speakers {mapping}; {len(turns)} turns")
            _save_transcript(Transcript(call_id=job.call_id, source="real", turns=turns))
        elif job.kind == "synthetic":
            from . import make_calls

            job.write(f"generating a synthetic call with persona {job.persona}")
            t = make_calls.synthetic_call(job.persona, make_calls.load_personas()[job.persona],
                                          get_settings().random_seed)
            job.call_id = t.call_id
        rows = pipeline.run_all(n=job.n, log=job.write, call_ids=[job.call_id])
        job.status = "done" if rows and rows[0]["status"] == "ok" else "failed"
    except llm.BudgetExceeded as e:
        job.write(f"budget guard: {e}")
        job.status = "failed"
    except Exception as e:
        job.write(f"error: {type(e).__name__}: {e}")
        job.write(traceback.format_exc(limit=3))
        job.status = "failed"
    job.write(f"finished: {job.status}. Estimated spend this server session ${llm.spend():.4f}")


def _worker() -> None:
    while True:
        _work(_q.get())


def _enqueue(job: Job) -> Job:
    JOBS[job.id] = job
    _q.put(job)
    return job


# ---------- helpers ----------

def _unique_id(base: str) -> str:
    base = re.sub(r"[^\w-]+", "_", base).strip("_")[:60] or "call"
    existing, cid, i = set(store.transcript_ids()), base, 2
    while cid in existing:
        cid, i = f"{base}_{i}", i + 1
    return cid


def _save_transcript(t: Transcript) -> None:
    (get_settings().transcripts_dir / f"{t.call_id}.json").write_text(t.model_dump_json(indent=2), encoding="utf-8")


def parse_pasted(text: str) -> list[Turn]:
    """Lines like 'bot: ...' / 'customer: ...' (also agent/user/b/c). Untagged lines continue the turn."""
    alias = {"bot": "bot", "agent": "bot", "b": "bot", "riya": "bot",
             "customer": "customer", "user": "customer", "c": "customer", "caller": "customer"}
    turns: list[Turn] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\s*([A-Za-z]+)\s*[:\-]\s*(.*)$", line)
        if m and m.group(1).lower() in alias:
            sp, body = alias[m.group(1).lower()], m.group(2).strip()
            if turns and turns[-1].speaker == sp:
                turns[-1].text += " " + body
            else:
                turns.append(Turn(idx=len(turns), speaker=sp, text=body))
        elif turns:
            turns[-1].text += " " + line
    return turns


async def _read_limited(f: UploadFile) -> bytes:
    data = await f.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file too large (25 MB max)")
    return data


def _page(template: str, ctx: dict | None = None, status: int = 200) -> HTMLResponse:
    return HTMLResponse(report.render(template, ctx or {}, urls=URLS, app_mode=True), status_code=status)


# ---------- app ----------

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _password()
    get_settings()  # validates Gemini model ids, fails fast
    threading.Thread(target=_worker, daemon=True).start()
    yield


app = FastAPI(title="Call Doctor", docs_url=None, redoc_url=None, lifespan=_lifespan)
PUBLIC = {"/login", "/healthz"}


@app.middleware("http")
async def _auth(request: Request, call_next):
    if request.url.path not in PUBLIC:
        if not hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()):
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_form() -> HTMLResponse:
    return _page("login.html")


@app.post("/login")
def login(password: str = Form(...)) -> Response:
    if not hmac.compare_digest(password, _password()):
        return _page("login.html", {"error": "Wrong password."}, status=401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, _token(), httponly=True, samesite="lax", secure=os.environ.get("RENDER") == "true",
                 max_age=60 * 60 * 24 * 14)
    return r


@app.post("/logout")
def logout() -> Response:
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return _page("index.html", report.index_context(URLS))


@app.get("/calls/{call_id}", response_class=HTMLResponse)
def call_page(call_id: str) -> HTMLResponse:
    if not _ID.match(call_id) or call_id not in store.transcript_ids():
        raise HTTPException(404, "unknown call")
    return _page("call.html", report.call_context(call_id, URLS))


@app.get("/audio/{name}")
def audio_file(name: str) -> FileResponse:
    if not re.match(r"^[\w-]+\.(mp3|wav)$", name):
        raise HTTPException(404)
    p = get_settings().reports_dir / "audio" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/new", response_class=HTMLResponse)
def new_form() -> HTMLResponse:
    from .make_calls import load_personas

    return _page("new.html", {"personas": list(load_personas()), "replay_n": get_settings().replay_n})


@app.post("/new")
async def new_submit(
    mode: str = Form(...),
    name: str = Form(""),
    n: int = Form(0),
    pasted: str = Form(""),
    persona: str = Form(""),
    transcript_file: UploadFile | None = File(None),
    audio_file: UploadFile | None = File(None),
) -> Response:
    def err(msg: str) -> HTMLResponse:
        from .make_calls import load_personas

        return _page("new.html", {"personas": list(load_personas()), "error": msg,
                                  "replay_n": get_settings().replay_n}, status=400)

    runs = n if 1 <= n <= 20 else None
    job = Job(id=uuid.uuid4().hex[:10], call_id="", kind=mode, n=runs)
    if mode == "json":
        if not transcript_file or not transcript_file.filename:
            return err("Choose a transcript .json file.")
        try:
            t = Transcript.model_validate_json(await _read_limited(transcript_file))
        except ValidationError as e:
            return err(f"Not a valid transcript: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}")
        t.call_id = _unique_id(name or t.call_id)
        _save_transcript(t)
        job.call_id = t.call_id
    elif mode == "paste":
        turns = parse_pasted(pasted)
        if len(turns) < 3 or not any(x.speaker == "bot" for x in turns):
            return err("Paste at least 3 lines like 'bot: ...' and 'customer: ...'.")
        t = Transcript(call_id=_unique_id(name or "pasted"), source="roleplay", turns=turns)
        _save_transcript(t)
        job.call_id = t.call_id
    elif mode == "audio":
        if not audio_file or not audio_file.filename:
            return err("Choose an audio recording.")
        rec = get_settings().recordings_dir
        rec.mkdir(parents=True, exist_ok=True)
        cid = _unique_id(name or Path(audio_file.filename).stem)
        suffix = Path(audio_file.filename).suffix.lower()
        if suffix not in {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".flac"}:
            return err("Unsupported audio type.")
        dest = rec / f"{cid}{suffix}"
        dest.write_bytes(await _read_limited(audio_file))
        job.call_id, job.audio_path = cid, str(dest)
    elif mode == "synthetic":
        from .make_calls import load_personas

        if persona not in load_personas():
            return err("Pick a persona.")
        job.persona, job.call_id = persona, f"syn_{persona}"
    else:
        return err("Unknown mode.")
    _enqueue(job)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.get("/jobs", response_class=HTMLResponse)
def jobs() -> HTMLResponse:
    js = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    return _page("jobs.html", {"jobs": js, "spend": llm.spend(), "budget": get_settings().max_spend_usd})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(job_id: str) -> HTMLResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job (jobs are kept in memory and reset on restart)")
    queued_ahead = sum(1 for j in JOBS.values() if j.status == "queued" and j.created < job.created)
    return _page("job.html", {"job": job, "queued_ahead": queued_ahead,
                              "has_report": job.call_id in store.transcript_ids()})
