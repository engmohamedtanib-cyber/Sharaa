"""Tool registration, argument validation, audit and idempotency.

Every rule that must hold for *all* tools is enforced here once, rather than
re-implemented (and eventually forgotten) in each handler:

* unknown or missing arguments are refused before the handler runs;
* money arrives as a string or an integer and becomes a :class:`Decimal` — a
  JSON float never reaches a monetary field;
* a write tool must carry ``request_id`` (idempotency) and ``verbatim``
  (what the user actually said), so no figure enters the ledger that the model
  originated on its own;
* the call is written to the append-only audit log whatever the outcome.

``call_tool`` does not raise for a refusal. It returns a failed
:class:`ToolResult` carrying the reason, because the agent's correct response to
"policy excludes this company" is to explain it, not to catch an exception.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from common.decimals import D
from tools.context import ToolContext
from tools.errors import ToolRefusal, ToolValidationError

ParamType = Literal["string", "money", "integer", "boolean", "number"]

Handler = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Param:
    """One tool argument."""

    name: str
    type: ParamType
    description: str
    required: bool = True
    default: Any = None


@dataclass(frozen=True)
class Tool:
    """A registered tool.

    ``write`` marks a tool that changes state. Write tools get the idempotency
    and provenance requirements; read tools get neither, because they have
    nothing to duplicate and nothing to attribute.
    """

    name: str
    description: str
    params: tuple[Param, ...]
    handler: Handler
    write: bool = False
    #: Write tools that record a *user-reported figure* also need the user's own
    #: words. A tool that only records an engine-computed figure does not.
    requires_verbatim: bool = False

    def schema(self) -> dict[str, Any]:
        """JSON-Schema-shaped description, as an MCP client expects."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            properties[p.name] = {
                # `money` is a string on the wire so exact decimals survive JSON.
                "type": "string" if p.type == "money" else p.type,
                "description": p.description,
            }
            if p.required:
                required.append(p.name)
        if self.write:
            properties["request_id"] = {
                "type": "string",
                "description": "Idempotency key. Re-sending the same id returns the first result "
                               "instead of writing twice.",
            }
            required.append("request_id")
            if self.requires_verbatim:
                properties["verbatim"] = {
                    "type": "string",
                    "description": "What the user actually said, in their words. Recorded for audit; "
                                   "the figures in this call must come from them, not from you.",
                }
                required.append("verbatim")
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": properties, "required": required},
        }


@dataclass(frozen=True)
class ToolResult:
    """What a tool call returns. Never a bare value — always an outcome."""

    ok: bool
    tool: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    kind: str = "OK"          # OK | REFUSED | INVALID | REPLAYED
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "tool": self.tool, "kind": self.kind, "data": self.data}
        if self.message:
            out["message"] = self.message
        if self.error:
            out["error"] = self.error
        return out


#: The one registry. Tool modules populate it at import time.
REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    """Add a tool, refusing a duplicate name.

    A silently overwritten tool would mean two different behaviours behind one
    name depending on import order — non-determinism at the API surface.
    """
    if tool.name in REGISTRY:
        raise ValueError(f"tool {tool.name!r} is already registered")
    REGISTRY[tool.name] = tool
    return tool


def tool_schemas() -> list[dict[str, Any]]:
    """Every registered tool, in stable name order."""
    return [REGISTRY[name].schema() for name in sorted(REGISTRY)]


# ======================================================================
# Argument coercion
# ======================================================================
def _coerce(param: Param, raw: Any) -> Any:
    if param.type == "money":
        if isinstance(raw, bool):
            raise ToolValidationError(f"{param.name}: expected an amount, got a boolean")
        if isinstance(raw, float):
            # Accepted, but converted through its string form so 0.1 stays 0.1.
            return D(str(raw))
        if isinstance(raw, Decimal | int | str):
            try:
                return D(raw)
            except ValueError as exc:
                raise ToolValidationError(f"{param.name}: {raw!r} is not an amount") from exc
        raise ToolValidationError(f"{param.name}: expected an amount, got {type(raw).__name__}")

    if param.type == "integer":
        if isinstance(raw, bool) or not isinstance(raw, int | str):
            raise ToolValidationError(f"{param.name}: expected an integer, got {type(raw).__name__}")
        try:
            return int(raw)
        except ValueError as exc:
            raise ToolValidationError(f"{param.name}: {raw!r} is not an integer") from exc

    if param.type == "number":
        if isinstance(raw, bool) or not isinstance(raw, int | float | str | Decimal):
            raise ToolValidationError(f"{param.name}: expected a number, got {type(raw).__name__}")
        try:
            return D(raw)
        except ValueError as exc:
            raise ToolValidationError(f"{param.name}: {raw!r} is not a number") from exc

    if param.type == "boolean":
        if not isinstance(raw, bool):
            raise ToolValidationError(f"{param.name}: expected true or false, got {type(raw).__name__}")
        return raw

    if not isinstance(raw, str):
        raise ToolValidationError(f"{param.name}: expected a string, got {type(raw).__name__}")
    return raw


def validate_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce and check arguments, refusing anything the tool did not declare."""
    known = {p.name for p in tool.params} | ({"request_id", "verbatim"} if tool.write else set())
    unknown = sorted(set(args) - known)
    if unknown:
        raise ToolValidationError(
            f"{tool.name}: unknown argument(s) {unknown}. Declared arguments are "
            f"{sorted(p.name for p in tool.params)}."
        )

    clean: dict[str, Any] = {}
    for p in tool.params:
        if p.name not in args or args[p.name] is None:
            if p.required:
                raise ToolValidationError(f"{tool.name}: missing required argument {p.name!r}")
            clean[p.name] = p.default
            continue
        clean[p.name] = _coerce(p, args[p.name])

    if tool.write:
        request_id = args.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ToolValidationError(
                f"{tool.name} writes to the ledger and needs a `request_id` so a retry cannot "
                "double-record."
            )
        clean["request_id"] = request_id.strip()
        if tool.requires_verbatim:
            verbatim = args.get("verbatim")
            if not isinstance(verbatim, str) or not verbatim.strip():
                raise ToolValidationError(
                    f"{tool.name} records a figure the user reported, so it needs `verbatim` — "
                    "their own words. A figure with no human source behind it must not enter the ledger."
                )
            clean["verbatim"] = verbatim.strip()
    return clean


# ======================================================================
# Dispatch
# ======================================================================
def call_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Validate, replay-or-run, audit, and return an outcome.

    Ordering is deliberate: the idempotency check happens *after* validation
    (a malformed retry is still malformed) and *before* the handler (that is the
    whole point).
    """
    tool = REGISTRY.get(name)
    if tool is None:
        available = ", ".join(sorted(REGISTRY))
        return ToolResult(
            ok=False, tool=name, kind="INVALID",
            error=f"no tool named {name!r}. Available: {available}",
        )

    try:
        clean = validate_args(tool, args)
    except ToolValidationError as exc:
        ctx.audit.append(at=ctx.now, tool=name, args=_loggable(args), outcome="ERROR", error=str(exc))
        return ToolResult(ok=False, tool=name, kind="INVALID", error=str(exc))

    request_id = clean.get("request_id") if tool.write else None

    if tool.write and isinstance(request_id, str):
        prior = ctx.audit.find_success(name, request_id)
        if prior is not None and prior.result is not None:
            return ToolResult(
                ok=True,
                tool=name,
                data=prior.result,
                kind="REPLAYED",
                message=f"Already recorded at {prior.at} (request_id {request_id}); nothing was written again.",
            )

    try:
        data = tool.handler(ctx, clean)
    except ToolRefusal as exc:
        ctx.audit.append(
            at=ctx.now, tool=name, args=_loggable(clean), outcome="REFUSED",
            request_id=request_id, error=str(exc),
        )
        return ToolResult(ok=False, tool=name, kind="REFUSED", error=str(exc))
    except (ToolValidationError, ValueError, RuntimeError) as exc:
        ctx.audit.append(
            at=ctx.now, tool=name, args=_loggable(clean), outcome="ERROR",
            request_id=request_id, error=f"{type(exc).__name__}: {exc}",
        )
        return ToolResult(ok=False, tool=name, kind="INVALID", error=f"{type(exc).__name__}: {exc}")

    message = str(data.pop("_message", ""))
    ctx.audit.append(
        at=ctx.now, tool=name, args=_loggable(clean), outcome="OK",
        request_id=request_id, result=data, store_result=tool.write,
    )
    return ToolResult(ok=True, tool=name, data=data, message=message)


def _loggable(args: dict[str, Any]) -> dict[str, Any]:
    """Arguments as JSON-safe values for the audit digest."""
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in args.items()}


def money(value: Decimal | None) -> str | None:
    """Serialise money for the wire: a string, never a float."""
    return None if value is None else str(value)


def money_map(values: dict[str, Decimal]) -> dict[str, str]:
    return {k: str(v) for k, v in sorted(values.items())}


def require_positive(value: Decimal, what: str) -> Decimal:
    if value <= 0:
        raise ToolRefusal(f"{what} must be positive (got {value})")
    return value


def as_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    if not ticker:
        raise ToolValidationError("a ticker is required")
    return ticker


def sorted_names(items: Sequence[str]) -> list[str]:
    return sorted(items)
