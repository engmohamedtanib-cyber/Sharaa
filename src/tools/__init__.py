"""The tool API — the seam between the reasoning plane and the truth plane.

``ARCHITECTURE_V2`` §3.1 splits the system into three planes. The LLM lives in
the conversation plane, holds no state and computes no numbers. This package is
the only way it may touch anything real.

Four rules hold for every tool in here, and they are enforced in
:mod:`tools.registry` rather than left to the discipline of each tool:

* **No tool accepts a financial figure the model invented.** Amounts either come
  from the user (confirmed, with their own words recorded verbatim) or from the
  deterministic engine. A write tool without ``verbatim`` is refused.
* **Every call is audited** — name, arguments, result digest, outcome — to an
  append-only log (R5).
* **Every write is idempotent** under retry, keyed by ``request_id``. A retried
  call returns the first result instead of writing twice.
* **Reads never mutate.** A read tool that writes is a bug, not a feature.

The tools are thin. All judgement lives in ``engine/``; a tool that starts
making decisions has escaped its layer.
"""

from __future__ import annotations

from tools.context import ToolContext
from tools.errors import ToolError, ToolRefusal, ToolValidationError
from tools.registry import REGISTRY, ToolResult, call_tool, tool_schemas

__all__ = [
    "REGISTRY",
    "ToolContext",
    "ToolError",
    "ToolRefusal",
    "ToolResult",
    "ToolValidationError",
    "call_tool",
    "tool_schemas",
]
