"""Tool-layer failures, separated by who has to do something about them.

The distinction matters because the agent's response differs: a validation error
is the agent's own fault and it should fix the call; a refusal is the *system*
declining, and the agent must relay the reason to the user rather than retry.
"""

from __future__ import annotations


class ToolError(RuntimeError):
    """Base class. Anything raised out of a tool is one of these."""


class ToolValidationError(ToolError):
    """The call was malformed — wrong shape, missing argument, bad type.

    The agent should correct the arguments and call again.
    """


class ToolRefusal(ToolError):
    """The system declines: a rule says no.

    Examples: an unpopulated universe, a policy that excludes the company, a
    figure the user has not confirmed, an unpayable buy. The agent must **not**
    retry — it must tell the user what the rule was.
    """


class ToolNotFoundError(ToolError):
    """No tool by that name is registered."""
