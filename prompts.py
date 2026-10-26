"""Every prompt the app sends a model lives in prompts.txt (format described at its top).

`get("tutor/core")` returns that section; `fill` swaps `{{name}}` placeholders in one pass (so a
value that itself contains `{{x}}` is never expanded). Read once at import, so a missing or
misspelled name fails in a test, not in a classroom.
"""
import re
from pathlib import Path


def _load(path):
    sections, name, lines = {}, None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("####"):
            continue
        header = re.fullmatch(r"### (\S+)\s*", line)
        if header:
            if name:
                sections[name] = "\n".join(lines).strip("\n")
            name, lines = header.group(1), []
        else:
            lines.append(line)
    if name:
        sections[name] = "\n".join(lines).strip("\n")
    return sections


_sections = _load(Path(__file__).parent / "prompts.txt")


def get(name):
    return _sections[name]


def has(name):
    return name in _sections


def fill(name, **values):
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]), _sections[name])
