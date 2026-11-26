"""Core AgentForge never imports the optional ``mcp`` SDK.

Two checks, because they fail differently:

* **Static:** no module outside ``forge/tools/mcp/{connector,auth}.py`` imports
  ``mcp``, ``mcp_types`` or ``httpx2``, and no core module imports
  ``forge.tools.mcp`` at all.
* **Dynamic:** importing ``forge`` and every core submodule in a fresh
  interpreter leaves ``mcp`` out of ``sys.modules``, and so does importing the
  SDK-free parts of ``forge.tools.mcp`` (hashing, approvals, config).
"""

from __future__ import annotations

import ast
import subprocess  # nosec B404 - runs this interpreter on a fixed script
import sys
from pathlib import Path

FORGE = Path(__file__).resolve().parents[1] / "forge"
SDK_MODULES = {"mcp", "mcp_types", "httpx2"}
SDK_USERS = {FORGE / "tools" / "mcp" / "connector.py", FORGE / "tools" / "mcp" / "auth.py"}
MCP_PACKAGE = FORGE / "tools" / "mcp"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_only_the_connector_and_auth_modules_import_the_sdk() -> None:
    offenders = []
    for path in sorted(FORGE.rglob("*.py")):
        roots = {name.split(".")[0] for name in _imports(path)}
        if roots & SDK_MODULES and path not in SDK_USERS:
            offenders.append(str(path.relative_to(FORGE)))
    assert offenders == []


def test_no_core_module_imports_the_mcp_package() -> None:
    offenders = []
    for path in sorted(FORGE.rglob("*.py")):
        if MCP_PACKAGE in path.parents:
            continue
        if any(
            name == "forge.tools.mcp" or name.startswith("forge.tools.mcp.")
            for name in _imports(path)
        ):
            offenders.append(str(path.relative_to(FORGE)))
    assert offenders == []


SCRIPT = r"""
import importlib, pkgutil, sys
import forge
skipped = []
for info in pkgutil.walk_packages(forge.__path__, "forge."):
    if info.name == "forge.tools.mcp" or info.name.startswith("forge.tools.mcp."):
        continue
    try:
        importlib.import_module(info.name)
    except ImportError as exc:  # an optional extra (an SDK) that is not installed
        skipped.append(info.name)
loaded = sorted(m for m in sys.modules if m.split(".")[0] in {"mcp", "mcp_types"})
assert not loaded, ("core imported the MCP SDK", loaded)
import forge.tools.mcp.hashing, forge.tools.mcp.approvals, forge.tools.mcp.config, forge.tools.mcp
loaded = sorted(m for m in sys.modules if m.split(".")[0] in {"mcp", "mcp_types"})
assert not loaded, ("the SDK-free MCP modules imported the SDK", loaded)
print("ok", len(skipped))
"""


def test_importing_core_never_loads_the_sdk() -> None:
    result = subprocess.run(  # noqa: S603  # nosec B603
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.startswith("ok")
