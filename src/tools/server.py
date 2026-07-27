"""MCP stdio server: the conversation plane's only door into the truth plane.

Implements the small subset of the Model Context Protocol that a tool server
needs — ``initialize``, ``tools/list``, ``tools/call`` — as line-delimited
JSON-RPC 2.0 over stdin/stdout. Written directly against the wire format rather
than against an SDK so the truth plane keeps zero runtime dependencies: this
process must be able to start on a machine where only PyYAML is installed.

Two deliberate properties:

* **``now`` is captured once per request**, at the boundary, and threaded
  through the whole call. Nothing below this file reads a clock.
* **Nothing here decides anything.** The server validates the envelope and
  dispatches; every refusal, every number and every message comes from the tool
  layer underneath.

Run it with::

    python -m tools.server --ledger memory/portfolio/ledger.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

# Importing the tool modules is what populates the registry.
import tools.analysis_tools
import tools.portfolio_tools  # noqa: F401  (registration side effect)
from tools.context import ToolContext, build_context
from tools.registry import call_tool, tool_schemas

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "egx-shariah-engine", "version": "0.1.0"}


def _now() -> str:
    """The single clock read in the whole system, at the outermost boundary."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def handle_request(request: dict[str, Any], make_context: Any) -> dict[str, Any] | None:
    """Turn one JSON-RPC request into a response, or ``None`` for a notification."""
    method = str(request.get("method", ""))
    request_id = request.get("id")

    if method.startswith("notifications/") or request_id is None:
        return None

    def ok(result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "tools/list":
        return ok({"tools": tool_schemas()})

    if method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name", ""))
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return err(-32602, "arguments must be an object")
        ctx: ToolContext = make_context()
        result = call_tool(name, args, ctx)
        # MCP carries tool failures as content with isError, not as protocol
        # errors: a refusal is a real answer the agent must relay, not a
        # transport fault.
        return ok({
            "content": [{"type": "text", "text": json.dumps(result.to_dict(), ensure_ascii=False)}],
            "isError": not result.ok,
        })

    return err(-32601, f"method not found: {method}")


def serve(stdin: TextIO, stdout: TextIO, make_context: Any) -> None:
    """Read requests until EOF. One JSON object per line."""
    for line in stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            request = json.loads(stripped)
        except json.JSONDecodeError as exc:
            stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }) + "\n")
            stdout.flush()
            continue
        response = handle_request(request, make_context)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EGX Shariah Engine MCP server (stdio)")
    parser.add_argument("--ledger", default=None, help="path to the portfolio ledger JSONL")
    parser.add_argument("--orders", default=None, help="path to the order-proposal JSONL")
    parser.add_argument("--decisions", default=None, help="path to the decision journal JSONL")
    parser.add_argument("--audit", default=None, help="path to the tool-call audit JSONL")
    args = parser.parse_args(argv)

    def make_context() -> ToolContext:
        return build_context(
            _now(),
            ledger_path=args.ledger,
            orders_path=args.orders,
            decisions_path=args.decisions,
            audit_path=args.audit,
        )

    serve(sys.stdin, sys.stdout, make_context)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
