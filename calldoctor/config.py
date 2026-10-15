"""Typed settings loaded from .env. Validates configured Gemini model IDs on first load."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
ROLES = ("bot", "customer", "diagnose", "patch", "judge")


class Settings(BaseModel):
    gemini_api_key: str
    models: dict[str, str]
    temperatures: dict[str, float]

    replay_n: int = 10
    replay_max_turns: int = 12
    regression_calls: int = 2
    random_seed: int = 42
    max_concurrency: int = 6
    max_spend_usd: float = 15.0
    judge_check_k: int = 10

    elevenlabs_api_key: str = ""
    elevenlabs_stt_model: str = ""
    elevenlabs_tts_model: str = ""
    elevenlabs_bot_voice_id: str = ""
    elevenlabs_customer_voice_id: str = ""
    voice_calls: list[str] = []

    transcripts_dir: Path
    recordings_dir: Path
    runs_dir: Path
    reports_dir: Path
    bot_prompt_path: Path
    taxonomy_path: Path
    prompts_dir: Path
    templates_dir: Path


def _req(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"[config] missing required env var {name} (see .env)")
    return val


def _path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default))
    return p if p.is_absolute() else ROOT / p


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    env = os.environ.get
    return Settings(
        gemini_api_key=_req("GEMINI_API_KEY"),
        models={r: _req(f"{r.upper()}_MODEL") for r in ROLES},
        temperatures={r: float(env(f"{r.upper()}_TEMPERATURE", "0.0")) for r in ROLES},
        replay_n=int(env("REPLAY_N", "10")),
        replay_max_turns=int(env("REPLAY_MAX_TURNS", "12")),
        regression_calls=int(env("REGRESSION_CALLS", "2")),
        random_seed=int(env("RANDOM_SEED", "42")),
        max_concurrency=int(env("MAX_CONCURRENCY", "6")),
        max_spend_usd=float(env("MAX_SPEND_USD", "15")),
        judge_check_k=int(env("JUDGE_CHECK_K", "10")),
        elevenlabs_api_key=env("ELEVENLABS_API_KEY", ""),
        elevenlabs_stt_model=env("ELEVENLABS_STT_MODEL", ""),
        elevenlabs_tts_model=env("ELEVENLABS_TTS_MODEL", ""),
        elevenlabs_bot_voice_id=env("ELEVENLABS_BOT_VOICE_ID", ""),
        elevenlabs_customer_voice_id=env("ELEVENLABS_CUSTOMER_VOICE_ID", ""),
        voice_calls=[c.strip() for c in env("VOICE_CALLS", "").split(",") if c.strip()],
        transcripts_dir=_path("TRANSCRIPTS_DIR", "transcripts"),
        recordings_dir=_path("RECORDINGS_DIR", "recordings"),
        runs_dir=_path("RUNS_DIR", "runs"),
        reports_dir=_path("REPORTS_DIR", "reports"),
        bot_prompt_path=_path("BOT_PROMPT_PATH", "bot_prompt.md"),
        taxonomy_path=_path("TAXONOMY_PATH", "taxonomy.yaml"),
        prompts_dir=ROOT / "prompts",
        templates_dir=ROOT / "templates",
    )


def verify_models(s: Settings) -> None:
    """Fail fast if any configured model ID is not served to this API key."""
    from google import genai

    client = genai.Client(api_key=s.gemini_api_key)
    available = sorted(
        m.name.removeprefix("models/")
        for m in client.models.list()
        if "generateContent" in (m.supported_actions or [])
    )
    bad = {role: m for role, m in s.models.items() if m.removeprefix("models/") not in available}
    if bad:
        lines = [f"  {role.upper()}_MODEL={m}" for role, m in bad.items()]
        sys.exit(
            "[config] unknown Gemini model ID(s):\n" + "\n".join(lines)
            + "\n\nAvailable model IDs for this key:\n  " + "\n  ".join(available)
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = load_settings()
    if os.environ.get("CALLDOCTOR_SKIP_MODEL_CHECK") != "1":
        verify_models(s)
    for d in (s.transcripts_dir, s.runs_dir, s.reports_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
