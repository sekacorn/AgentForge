"""Single source of truth for the package version.

Hatchling reads ``__version__`` from this file at build time (see
``[tool.hatch.version]`` in ``pyproject.toml``), and ``forge.__init__`` re-exports
it for runtime access via ``forge.__version__``.
"""

__version__ = "0.3.0"
