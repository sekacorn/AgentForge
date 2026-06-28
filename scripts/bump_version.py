#!/usr/bin/env python3
"""Bump the project version in ``pyproject.toml`` and ``forge/_version.py``.

Usage::

    python scripts/bump_version.py 0.2.0

Both files are updated atomically (write to a temp file, then ``os.replace``) so
the packaged version and the runtime ``forge.__version__`` stay in sync.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_VERSION_FILE = _ROOT / "forge" / "_version.py"

# Accept X.Y.Z with an optional pre-release / post / build suffix (e.g. 0.2.0rc1).
_SEMVER = re.compile(r"^\d+\.\d+\.\d+([.\-+][0-9A-Za-z.\-]+)?$")


def _atomic_write(path: Path, content: str) -> None:
    """Replace ``path`` with ``content`` atomically."""
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _replace_version(path: Path, pattern: re.Pattern[str], replacement: str) -> str:
    text = path.read_text(encoding="utf-8")
    new_text, count = pattern.subn(replacement, text)
    if count != 1:
        raise SystemExit(f"error: expected exactly one version line in {path}, found {count}")
    return new_text


def bump(version: str) -> None:
    if not _SEMVER.match(version):
        raise SystemExit(f"error: {version!r} is not a valid X.Y.Z version")

    pyproject = _replace_version(
        _PYPROJECT,
        re.compile(r'(?m)^version = "[^"]*"'),
        f'version = "{version}"',
    )
    version_file = _replace_version(
        _VERSION_FILE,
        re.compile(r'(?m)^__version__ = "[^"]*"'),
        f'__version__ = "{version}"',
    )

    # Write only after both replacements succeed, so a bad file can't leave the
    # two version sources out of sync.
    _atomic_write(_PYPROJECT, pyproject)
    _atomic_write(_VERSION_FILE, version_file)

    print(f"Bumped version to {version}")
    print(f"  updated {_PYPROJECT.relative_to(_ROOT)}")
    print(f"  updated {_VERSION_FILE.relative_to(_ROOT)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bump the Forge (agentforge-oss) version.")
    parser.add_argument("version", help="New version, e.g. 0.2.0")
    args = parser.parse_args(argv)
    bump(args.version)


if __name__ == "__main__":
    main()
