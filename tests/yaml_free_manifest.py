"""Tiny reader for the two list fields the tests need, so the tests need no YAML package."""
from pathlib import Path


def load(path: Path) -> dict:
    out, current = {}, None
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.rstrip().endswith(":"):
            current = line.strip()[:-1]
            out[current] = []
        elif not line.startswith(" "):
            current = None
        elif current is not None and line.strip().startswith("- ") and line.startswith("  - "):
            out[current].append(line.strip()[2:].strip())
    return out
