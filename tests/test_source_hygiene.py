"""No source file carries characters that make code read differently from how it runs."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Bidirectional embeddings, overrides and isolates (CVE-2021-42574, "Trojan Source"),
#: line and paragraph separators, and the escape character. Tests that need them use
#: escape sequences (backslash, "u", then the code point) in string literals.
FORBIDDEN = {0x1B, 0x2028, 0x2029, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
SUFFIXES = {".py", ".md", ".json", ".toml", ".yml", ".yaml"}


def test_no_bidirectional_or_escape_characters_in_sources() -> None:
    offenders = []
    for folder in ("forge", "tests", "docs", "examples"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix in SUFFIXES and path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                found = sorted({f"U+{ord(c):04X}" for c in text if ord(c) in FORBIDDEN})
                if found:
                    offenders.append(f"{path.relative_to(ROOT)}: {', '.join(found)}")
    assert offenders == []
