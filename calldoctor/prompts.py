"""Load internal prompts from prompts/*.md and fill them with jinja2."""
from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import ROOT

_env = Environment(
    loader=FileSystemLoader(ROOT / "prompts"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    autoescape=False,
)


def render(name: str, **kw) -> str:
    return _env.get_template(name).render(**kw)
