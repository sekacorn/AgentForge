"""Built-in tools shipped with Forge.

``calculator`` and ``utc_now`` are pure and safe. ``http_get`` performs network
egress and is therefore marked ``dangerous`` so the security layer can gate it.
"""

from forge.tools.builtin.calculator import calculator
from forge.tools.builtin.http import http_get
from forge.tools.builtin.time import utc_now

#: Convenient bundle of safe, side-effect-free tools.
SAFE_TOOLS = [calculator, utc_now]

__all__ = ["calculator", "http_get", "utc_now", "SAFE_TOOLS"]
