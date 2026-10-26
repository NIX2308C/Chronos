"""Every prompt the app sends a model lives in prompts.md (format described at its top).

`get("tutor/core")` returns that section; `fill` swaps `{{name}}` placeholders in one pass (so a
value that itself contains `{{x}}` is never expanded). Read once at import, so a missing or
misspelled name fails in a test, not in a classroom.
"""
import re
from pathlib import Path


def _load(path):
    """`## name` heading, then the fenced block under it is that prompt."""
    sections, name, lines = {}, None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if lines is None:
            heading = re.fullmatch(r"## (\S+)\s*", line)
            if heading:
                name = heading.group(1)
            elif name and line.startswith("```"):
                lines = []
        elif line.startswith("```"):
            sections[name], name, lines = "\n".join(lines).strip("\n"), None, None
        else:
            lines.append(line)
    return sections


_sections = _load(Path(__file__).parent / "prompts.md")


def get(name):
    return _sections[name]


def has(name):
    return name in _sections


def fill(name, **values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]), _sections[name])
