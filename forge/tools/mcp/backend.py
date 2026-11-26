"""What a tool's function depends on, for ``tool-inventory.v1``. No ``mcp`` import.

Every tool in an inventory declares a backend:

* **self-contained** (``null`` in the inventory): its function depends on no
  external service;
* otherwise the service it depends on, as ``{system, portability,
  reconstruction}``. That includes a vendor API behind a third-party MCP server,
  and whatever a ``ToolConnector`` adapter wraps.

A vendor-only backend scores the tool 75 (documented reconstruction) or 100
(none) in the suite, whatever its MCP interface, approval or hosting. The
declaration is always attested: nothing can see behind a tool.

In ``mcp-servers.toml`` (TOML has no null) a server says
``backend = "self-contained"`` or gives a table::

    backend = { system = "crm-vendor-api", portability = "vendor_only", reconstruction = "documented" }

and may override it per tool in ``tool_backends``.
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, model_validator

SELF_CONTAINED: Final = "self-contained"
_SYSTEM: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")


class Backend(BaseModel):
    """An external service a tool's function depends on."""

    system: str
    portability: Literal["vendor_only", "mcp"]
    #: ``documented`` or ``none`` for a vendor-only service; ``None`` for an MCP one.
    reconstruction: Literal["documented", "none"] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _shape(self) -> Backend:
        if not _SYSTEM.fullmatch(self.system):
            raise ValueError("A backend system is an identifier, never a URL or credential.")
        if self.portability == "vendor_only" and self.reconstruction is None:
            raise ValueError(
                "A vendor-only backend says whether reconstruction is documented or none."
            )
        if self.portability == "mcp" and self.reconstruction is not None:
            raise ValueError("An mcp backend has no reconstruction path.")
        return self

    def to_inventory(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "portability": self.portability,
            "reconstruction": self.reconstruction,
        }


BackendDeclaration = Backend | Literal["self-contained"]


def inventory_backend(declaration: BackendDeclaration) -> dict[str, Any] | None:
    """The ``backend`` value written into ``tool-inventory.v1``."""
    if isinstance(declaration, Backend):
        return declaration.to_inventory()
    return None
