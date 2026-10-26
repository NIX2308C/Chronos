"""Every prompt the app sends a model lives in prompts/ as a markdown file.

`get("tutor/core")` returns prompts/tutor/core.md; `fill` swaps `{{name}}` placeholders in one
pass (so a value that itself contains `{{x}}` is never expanded). All files are read once at
import, so a missing or misspelled name fails the first request in a test, not in a classroom.
See prompts/README.md for the layout.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent / "prompts"
_files = {p.relative_to(ROOT).with_suffix("").as_posix(): p.read_text(encoding="utf-8").strip("\n")
          for p in ROOT.rglob("*.md") if p.name != "README.md"}


def get(name):
    return _files[name]


def has(name):
    return name in _files


def fill(name, **values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]), _files[name])
