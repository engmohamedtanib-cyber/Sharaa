"""Shared shape for every routine: result, idempotency, quiet discipline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tools.context import ToolContext


@dataclass(frozen=True)
class RoutineResult:
    """What one routine run produced.

    ``message`` is what the user sees; empty means the routine chose silence.
    ``quiet`` records that the silence (or the "nothing to do" note) was a
    decision, so a run that did nothing is distinguishable from a run that
    crashed before doing anything.
    """

    routine: str
    at: str
    message: str = ""
    quiet: bool = False
    details: dict[str, object] = field(default_factory=dict)
    failures: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "routine": self.routine,
            "at": self.at,
            "quiet": self.quiet,
            "details": self.details,
        }
        if self.message:
            out["message"] = self.message
        if self.failures:
            out["failures"] = list(self.failures)
        return out


#: A scheduled unit of work: context in, result out. Nothing else.
Routine = Callable[[ToolContext], RoutineResult]


def run_key(routine: str, now: str) -> str:
    """One run per routine per day. The unit of 'already done'."""
    return f"routine:{routine}:{now[:10]}"


def already_ran(ctx: ToolContext, routine: str) -> bool:
    """True when this routine already completed today.

    Checked against the audit log, which is append-only, so this survives a
    restart: a container that dies mid-afternoon and comes back does not re-run
    the morning's poll and re-announce yesterday's filing as new.
    """
    return ctx.audit.find_success("routine", run_key(routine, ctx.now)) is not None


def record_run(ctx: ToolContext, result: RoutineResult) -> RoutineResult:
    """Write the run to the audit log, making it idempotent for the rest of the day."""
    ctx.audit.append(
        at=ctx.now,
        tool="routine",
        args={"routine": result.routine, "date": ctx.now[:10]},
        outcome="OK" if result.clean else "REFUSED",
        request_id=run_key(result.routine, ctx.now),
        result=result.to_dict(),
        store_result=True,
    )
    return result


def guard(routine: str) -> Callable[[Callable[[ToolContext], RoutineResult]], Callable[[ToolContext], RoutineResult]]:
    """Wrap a routine body with the once-a-day check and the audit write."""

    def decorate(body: Callable[[ToolContext], RoutineResult]) -> Callable[[ToolContext], RoutineResult]:
        def wrapper(ctx: ToolContext) -> RoutineResult:
            if already_ran(ctx, routine):
                return RoutineResult(
                    routine=routine,
                    at=ctx.now,
                    quiet=True,
                    message="",
                    details={"skipped": "already ran today"},
                )
            return record_run(ctx, body(ctx))

        wrapper.__name__ = body.__name__
        wrapper.__doc__ = body.__doc__
        return wrapper

    return decorate
