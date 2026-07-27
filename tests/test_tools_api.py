"""The tool API: validation, audit, idempotency, refusals, and the confirm loop.

The end-to-end test at the bottom is the one that matters most — it is the
conversation from ``ARCHITECTURE_V2`` §2 executed against real files:
contribute, plan, confirm a fill, ask what you own.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import tools.analysis_tools
import tools.portfolio_tools  # noqa: F401  (tool registration)
from config_loader import load_policy, load_thresholds, load_universe
from engine.universe import Constituent, Provenance, Universe, UniverseStatus
from reporting.order_sheet import Side, Validity
from store.jsonl_decisions import JsonlDecisionStore
from store.jsonl_ledger import JsonlLedgerStore
from store.jsonl_orders import JsonlOrderStore, OrderAction, OrderStoreError
from tools.audit import AuditLog, digest
from tools.context import ToolContext
from tools.data import CompanyFacts, InMemoryDataProvider, NoDataProvider
from tools.registry import REGISTRY, call_tool, tool_schemas
from tools.server import handle_request

NOW = "2026-07-27T09:00:00+00:00"


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        now=NOW,
        ledger=JsonlLedgerStore(tmp_path / "ledger.jsonl"),
        orders=JsonlOrderStore(tmp_path / "orders.jsonl"),
        decisions=JsonlDecisionStore(tmp_path / "journal.jsonl"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        cfg=load_thresholds(),
        policy=load_policy(),
        universe=load_universe(),
        prices={},
        data=NoDataProvider(),
    )


def _call(ctx: ToolContext, name: str, **args: Any) -> Any:
    return call_tool(name, args, ctx)


# ----------------------------------------------------------------------
# Registry and schemas
# ----------------------------------------------------------------------
def test_every_write_tool_demands_an_idempotency_key() -> None:
    for tool in REGISTRY.values():
        if tool.write:
            required = tool.schema()["inputSchema"]["required"]
            assert "request_id" in required, f"{tool.name} can be retried into a double write"


def test_tools_that_record_user_figures_demand_the_users_words() -> None:
    for name in ("record_contribution", "record_dividend", "confirm_order_fill", "settle_purification"):
        assert "verbatim" in REGISTRY[name].schema()["inputSchema"]["required"]


def test_schemas_are_stable_and_named() -> None:
    schemas = tool_schemas()
    names = [s["name"] for s in schemas]
    assert names == sorted(names)
    assert "get_portfolio" in names
    assert all(s["inputSchema"]["type"] == "object" for s in schemas)


def test_money_is_a_string_on_the_wire() -> None:
    schema = REGISTRY["record_contribution"].schema()["inputSchema"]
    assert schema["properties"]["amount"]["type"] == "string"


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def test_unknown_tool_is_reported_with_the_available_list(ctx: ToolContext) -> None:
    result = _call(ctx, "make_me_rich")
    assert result.ok is False
    assert result.kind == "INVALID"
    assert "get_portfolio" in (result.error or "")


def test_unknown_argument_is_refused(ctx: ToolContext) -> None:
    result = _call(ctx, "get_position", ticker="COMI", leverage="10x")
    assert result.kind == "INVALID"
    assert "leverage" in (result.error or "")


def test_missing_required_argument_is_refused(ctx: ToolContext) -> None:
    assert _call(ctx, "get_position").kind == "INVALID"


def test_write_without_request_id_is_refused(ctx: ToolContext) -> None:
    result = _call(ctx, "record_contribution", amount="1000", verbatim="I added 1000")
    assert result.kind == "INVALID"
    assert "request_id" in (result.error or "")


def test_write_without_verbatim_is_refused(ctx: ToolContext) -> None:
    result = _call(ctx, "record_contribution", amount="1000", request_id="r1")
    assert result.kind == "INVALID"
    assert "their own words" in (result.error or "")


def test_non_numeric_money_is_refused(ctx: ToolContext) -> None:
    result = _call(ctx, "record_contribution", amount="a lot", request_id="r1", verbatim="a lot")
    assert result.kind == "INVALID"


def test_boolean_is_not_an_amount(ctx: ToolContext) -> None:
    result = _call(ctx, "record_contribution", amount=True, request_id="r1", verbatim="x")
    assert result.kind == "INVALID"


def test_float_amount_keeps_its_decimal_value(ctx: ToolContext) -> None:
    result = _call(ctx, "record_contribution", amount=1000.10, request_id="r1", verbatim="I added 1000.10")
    assert result.ok
    # Exact value, not binary noise: 1000.10 must not arrive as 1000.0999999...
    assert Decimal(result.data["amount"]) == Decimal("1000.10")
    assert ctx.ledger.state().cash == Decimal("1000.10")


def test_wrong_types_are_refused(ctx: ToolContext) -> None:
    assert _call(ctx, "get_ledger_history", limit=[1]).kind == "INVALID"
    assert _call(ctx, "get_order_proposals", include_closed="yes").kind == "INVALID"
    assert _call(ctx, "get_position", ticker=42).kind == "INVALID"


# ----------------------------------------------------------------------
# Audit and idempotency
# ----------------------------------------------------------------------
def test_every_call_is_audited(ctx: ToolContext) -> None:
    _call(ctx, "get_portfolio")
    _call(ctx, "get_position", ticker="COMI")
    records = ctx.audit.read_all()
    assert [r.tool for r in records] == ["get_portfolio", "get_position"]
    assert [r.outcome for r in records] == ["OK", "OK"]
    assert [r.seq for r in records] == [1, 2]


def test_refusals_and_errors_are_audited_too(ctx: ToolContext) -> None:
    _call(ctx, "record_contribution", amount="-5", request_id="r1", verbatim="minus five")
    _call(ctx, "get_position")
    outcomes = [r.outcome for r in ctx.audit.read_all()]
    assert outcomes == ["REFUSED", "ERROR"]


def test_retrying_a_write_returns_the_first_result_and_writes_once(ctx: ToolContext) -> None:
    first = _call(ctx, "record_contribution", amount="25000", request_id="abc", verbatim="I added 25,000")
    second = _call(ctx, "record_contribution", amount="25000", request_id="abc", verbatim="I added 25,000")
    assert first.ok and second.ok
    assert second.kind == "REPLAYED"
    assert second.data == first.data
    assert ctx.ledger.state().cash == Decimal("25000")
    assert len(ctx.ledger.read_all()) == 1


def test_the_same_request_id_on_a_different_tool_is_not_replayed(ctx: ToolContext) -> None:
    _call(ctx, "record_contribution", amount="25000", request_id="same", verbatim="added")
    result = _call(ctx, "record_withdrawal", amount="100", request_id="same", verbatim="took 100 out")
    assert result.kind == "OK"
    assert ctx.ledger.state().cash == Decimal("24900")


def test_audit_digest_detects_an_edited_result(ctx: ToolContext) -> None:
    _call(ctx, "record_contribution", amount="1000", request_id="r1", verbatim="added 1000")
    record = ctx.audit.read_all()[0]
    assert record.result is not None
    assert record.result_digest == digest(record.result)
    tampered = dict(record.result, amount="9999")
    assert digest(tampered) != record.result_digest


# ----------------------------------------------------------------------
# Ledger refusals surface as refusals, not crashes
# ----------------------------------------------------------------------
def test_withdrawing_more_than_the_cash_is_refused(ctx: ToolContext) -> None:
    _call(ctx, "record_contribution", amount="100", request_id="r1", verbatim="added 100")
    result = _call(ctx, "record_withdrawal", amount="500", request_id="r2", verbatim="took 500")
    assert result.kind == "REFUSED"
    assert ctx.ledger.state().cash == Decimal("100")


def test_settling_more_purification_than_is_due_is_refused(ctx: ToolContext) -> None:
    result = _call(ctx, "settle_purification", amount="50", request_id="r1", verbatim="gave 50 to charity")
    assert result.kind == "REFUSED"
    assert "exceeds" in (result.error or "")


def test_corporate_action_on_an_unheld_company_is_refused(ctx: ToolContext) -> None:
    result = _call(
        ctx, "record_corporate_action", ticker="COMI", kind="SPLIT", ratio="2",
        request_id="r1", verbatim="COMI split 2 for 1",
    )
    assert result.kind == "REFUSED"


# ----------------------------------------------------------------------
# No data / no universe: refusals that name what is missing
# ----------------------------------------------------------------------
def test_screening_without_data_refuses_and_says_what_it_needs(ctx: ToolContext) -> None:
    result = _call(ctx, "get_compliance_status", ticker="COMI")
    assert result.kind == "REFUSED"
    assert "financial statements" in (result.error or "")


def test_universe_wide_screening_refuses_while_the_universe_is_empty(ctx: ToolContext) -> None:
    result = _call(ctx, "run_screening")
    assert result.kind == "REFUSED"
    assert "never been transcribed" in (result.error or "")


def test_universe_status_explains_the_gap_rather_than_claiming_nothing_is_compliant(ctx: ToolContext) -> None:
    result = _call(ctx, "get_universe_status")
    assert result.ok
    assert result.data["available"] is False
    assert "not the same as" in result.message


def test_planning_refuses_without_a_universe(ctx: ToolContext) -> None:
    assert _call(ctx, "propose_investment_plan", request_id="p1").kind == "REFUSED"


def test_explaining_an_unknown_decision_is_refused(ctx: ToolContext) -> None:
    assert _call(ctx, "explain_decision", decision_id="nope").kind == "REFUSED"


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------
def test_empty_portfolio_reads_cleanly(ctx: ToolContext) -> None:
    result = _call(ctx, "get_portfolio")
    assert result.ok
    assert result.data["cash"] == "0"
    assert result.data["holdings"] == []
    assert result.data["events_recorded"] == 0


def test_unpriced_holdings_are_surfaced_not_valued_at_cost(tmp_path: Path, ctx: ToolContext) -> None:
    _seed_position(ctx)
    result = _call(ctx, "get_portfolio")
    assert result.data["unpriced_holdings"] == ["AAAA"]
    assert result.data["valuation_complete"] is False
    assert result.data["holdings"][0]["market_value"] is None
    assert "not valued at cost" in result.message


def test_priced_holdings_carry_weights(ctx: ToolContext) -> None:
    _seed_position(ctx)
    priced = ToolContext(**{**ctx.__dict__, "prices": {"AAAA": Decimal("12")}})
    result = _call(priced, "get_portfolio")
    holding = result.data["holdings"][0]
    assert holding["market_value"] == "1200"
    assert result.data["valuation_complete"] is True
    assert Decimal(holding["weight"]) > 0


def test_position_and_history_reads(ctx: ToolContext) -> None:
    _seed_position(ctx)
    position = _call(ctx, "get_position", ticker="aaaa")
    assert position.data["held"] is True
    assert position.data["shares"] == "100"

    missing = _call(ctx, "get_position", ticker="ZZZZ")
    assert missing.data["held"] is False

    history = _call(ctx, "get_ledger_history", ticker="AAAA", limit=5)
    assert history.data["count"] == 1
    assert history.data["events"][0]["type"] == "BUY_FILLED"


def test_purification_balance_reads_zero_cleanly(ctx: ToolContext) -> None:
    result = _call(ctx, "get_purification_balance")
    assert result.data["due"] == "0"
    assert "Nothing outstanding" in result.message


def test_policy_read_reports_effective_limits(ctx: ToolContext) -> None:
    result = _call(ctx, "get_policy")
    assert result.data["version"] == 1
    assert result.data["require_shariah_gate"] is True
    assert result.data["effective_limits"]["max_single_position"] == str(ctx.cfg.constraint("max_single_position"))


# ----------------------------------------------------------------------
# The order confirm loop
# ----------------------------------------------------------------------
def _seed_position(ctx: ToolContext, ticker: str = "AAAA") -> str:
    """Contribute cash, propose an order, and fill it. Returns the order id."""
    _call(ctx, "record_contribution", amount="25000", request_id="c1", verbatim="I added 25,000")
    ctx.orders.propose(
        order_id=f"{ticker}-1",
        at=NOW,
        ticker=ticker,
        side=Side.BUY,
        quantity=Decimal("100"),
        limit_price=Decimal("10.50"),
        validity=Validity.DAY,
    )
    _call(
        ctx, "confirm_order_fill", order_id=f"{ticker}-1", shares="100", price="10.00", fees="20",
        request_id="f1", verbatim="done, bought 100 at 10.00, fees 20",
    )
    return f"{ticker}-1"


def test_confirming_a_fill_records_shares_cash_and_closes_the_proposal(ctx: ToolContext) -> None:
    order_id = _seed_position(ctx)
    state = ctx.ledger.state()
    assert state.shares_of("AAAA") == Decimal("100")
    assert state.cash == Decimal("25000") - Decimal("1000") - Decimal("20")
    order = ctx.orders.get(order_id)
    assert order is not None
    assert order.status.value == "FILLED"


def test_a_partial_fill_leaves_the_proposal_open(ctx: ToolContext) -> None:
    _call(ctx, "record_contribution", amount="25000", request_id="c1", verbatim="added 25,000")
    ctx.orders.propose(
        order_id="AAAA-1", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("100"), limit_price=Decimal("10.50"), validity=Validity.DAY,
    )
    first = _call(
        ctx, "confirm_order_fill", order_id="AAAA-1", shares="40", price="10.00",
        request_id="f1", verbatim="only 40 filled",
    )
    assert first.data["status"] == "PARTIALLY_FILLED"
    assert first.data["outstanding"] == "60"

    second = _call(
        ctx, "confirm_order_fill", order_id="AAAA-1", shares="60", price="10.10",
        request_id="f2", verbatim="the rest filled at 10.10",
    )
    assert second.data["status"] == "FILLED"
    assert ctx.ledger.state().shares_of("AAAA") == Decimal("100")


def test_overfilling_a_proposal_is_refused(ctx: ToolContext) -> None:
    _call(ctx, "record_contribution", amount="25000", request_id="c1", verbatim="added")
    ctx.orders.propose(
        order_id="AAAA-1", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("100"), limit_price=Decimal("10.50"), validity=Validity.DAY,
    )
    result = _call(
        ctx, "confirm_order_fill", order_id="AAAA-1", shares="150", price="10.00",
        request_id="f1", verbatim="bought 150",
    )
    assert result.kind == "REFUSED"
    assert ctx.ledger.state().shares_of("AAAA") == 0


def test_a_fill_without_a_proposal_is_refused(ctx: ToolContext) -> None:
    result = _call(
        ctx, "confirm_order_fill", order_id="ghost", shares="10", price="1",
        request_id="f1", verbatim="bought some",
    )
    assert result.kind == "REFUSED"
    assert "propose the order first" in (result.error or "")


def test_an_unpayable_fill_is_refused_and_the_proposal_stays_open(ctx: ToolContext) -> None:
    ctx.orders.propose(
        order_id="AAAA-1", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("100"), limit_price=Decimal("10.50"), validity=Validity.DAY,
    )
    result = _call(
        ctx, "confirm_order_fill", order_id="AAAA-1", shares="100", price="10.00",
        request_id="f1", verbatim="bought 100",
    )
    assert result.kind == "REFUSED"
    order = ctx.orders.get("AAAA-1")
    assert order is not None and order.open


def test_cancelling_and_expiring_a_proposal(ctx: ToolContext) -> None:
    ctx.orders.propose(
        order_id="AAAA-1", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("10"), limit_price=Decimal("10"), validity=Validity.DAY,
    )
    cancelled = _call(ctx, "cancel_order_proposal", order_id="AAAA-1", request_id="x1")
    assert cancelled.data["status"] == "CANCELLED"
    again = _call(ctx, "cancel_order_proposal", order_id="AAAA-1", request_id="x2")
    assert again.kind == "REFUSED"

    ctx.orders.propose(
        order_id="BBBB-1", at=NOW, ticker="BBBB", side=Side.BUY,
        quantity=Decimal("10"), limit_price=Decimal("10"), validity=Validity.DAY,
    )
    expired = _call(ctx, "cancel_order_proposal", order_id="BBBB-1", expired=True, request_id="x3")
    assert expired.data["status"] == "EXPIRED"


def test_open_proposals_are_listed_and_closed_ones_hidden(ctx: ToolContext) -> None:
    order_id = _seed_position(ctx)
    open_only = _call(ctx, "get_order_proposals")
    assert open_only.data["count"] == 0
    with_closed = _call(ctx, "get_order_proposals", include_closed=True)
    assert [o["order_id"] for o in with_closed.data["orders"]] == [order_id]


# ----------------------------------------------------------------------
# Corporate actions and purification
# ----------------------------------------------------------------------
def test_a_split_scales_shares_and_preserves_cost_basis(ctx: ToolContext) -> None:
    _seed_position(ctx)
    before = ctx.ledger.state().holdings["AAAA"].cost_basis
    result = _call(
        ctx, "record_corporate_action", ticker="AAAA", kind="SPLIT", ratio="2",
        request_id="s1", verbatim="AAAA split two for one",
    )
    assert result.data["shares_after"] == "200"
    assert ctx.ledger.state().holdings["AAAA"].cost_basis == before


def test_an_unknown_corporate_action_kind_is_refused(ctx: ToolContext) -> None:
    _seed_position(ctx)
    result = _call(
        ctx, "record_corporate_action", ticker="AAAA", kind="MERGER", ratio="2",
        request_id="s1", verbatim="merger",
    )
    assert result.kind == "REFUSED"


# ----------------------------------------------------------------------
# Order store invariants
# ----------------------------------------------------------------------
def test_order_store_rejects_an_impossible_history(tmp_path: Path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    store.propose(
        order_id="A-1", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("10"), limit_price=Decimal("5"), validity=Validity.DAY,
    )
    store.record_action(order_id="A-1", at=NOW, action=OrderAction.FILLED, filled_quantity=Decimal("10"))
    with pytest.raises(OrderStoreError):
        store.record_action(order_id="A-1", at=NOW, action=OrderAction.FILLED, filled_quantity=Decimal("1"))
    with pytest.raises(OrderStoreError):
        store.record_action(order_id="B-9", at=NOW, action=OrderAction.CANCELLED)
    with pytest.raises(OrderStoreError):
        store.propose(
            order_id="A-1", at=NOW, ticker="AAAA", side=Side.BUY,
            quantity=Decimal("10"), limit_price=Decimal("5"), validity=Validity.DAY,
        )


def test_order_store_refuses_a_malformed_proposal(tmp_path: Path) -> None:
    store = JsonlOrderStore(tmp_path / "orders.jsonl")
    with pytest.raises(OrderStoreError):
        store.append({"order_id": "A-1", "at": NOW, "action": "PROPOSED", "ticker": "AAAA", "side": "BUY"})
    with pytest.raises(OrderStoreError):
        store.append({"order_id": "A-2", "at": NOW, "action": "NOT_A_THING"})


# ----------------------------------------------------------------------
# Screening and planning with seeded data
# ----------------------------------------------------------------------
def _seeded_context(ctx: ToolContext, facts: CompanyFacts, price: Decimal) -> ToolContext:
    universe = Universe(
        version="test",
        status=UniverseStatus.POPULATED,
        index_name="Test index",
        expected_count=None,
        constituents=(Constituent(facts.ticker, "Test Co", sector=facts.sector),),
        off_index_watch=(),
        retrieved=Provenance("2026-07-27", "fixture", "test"),
    )
    return ToolContext(**{**ctx.__dict__, "universe": universe,
                          "data": InMemoryDataProvider({facts.ticker: facts}),
                          "prices": {facts.ticker: price}})


def test_screening_a_seeded_company_reports_headroom(ctx: ToolContext, clean_company: CompanyFacts) -> None:
    seeded = _seeded_context(ctx, clean_company, Decimal("12"))
    result = call_tool("get_compliance_status", {"ticker": clean_company.ticker}, seeded)
    assert result.ok
    assert result.data["status"] in {"GREEN", "AMBER", "RED"}
    assert len(result.data["screens"]) == 5
    assert all(s["headroom"] is not None or s["utilisation"] is None for s in result.data["screens"])


def test_universe_screening_lists_companies_it_could_not_check(
    ctx: ToolContext, clean_company: CompanyFacts
) -> None:
    seeded = _seeded_context(ctx, clean_company, Decimal("12"))
    universe = Universe(
        **{**seeded.universe.__dict__,
           "constituents": (*seeded.universe.constituents, Constituent("ZZZZ", "Unknown Co"))}
    )
    widened = ToolContext(**{**seeded.__dict__, "universe": universe})
    result = call_tool("run_screening", {}, widened)
    assert result.ok
    assert result.data["skipped"] == ["ZZZZ"]
    assert "DATA_INSUFFICIENT, not as failures" in result.message


def test_planning_refuses_loudly_while_execution_fees_are_unset(
    ctx: ToolContext, clean_company: CompanyFacts
) -> None:
    """Fees are null in config on purpose; sizing must stop rather than guess."""
    seeded = _seeded_context(ctx, clean_company, Decimal("12"))
    call_tool("record_contribution", {"amount": "50000", "request_id": "c1", "verbatim": "added"}, seeded)
    result = call_tool("propose_investment_plan", {"request_id": "p1"}, seeded)
    assert result.kind == "REFUSED"
    assert "execution fees" in (result.error or "")


def test_planning_refuses_when_there_is_no_cash(ctx: ToolContext, clean_company: CompanyFacts) -> None:
    seeded = _seeded_context(ctx, clean_company, Decimal("12"))
    result = call_tool("propose_investment_plan", {"request_id": "p1"}, seeded)
    assert result.kind == "REFUSED"
    assert "nothing to deploy" in (result.error or "")


def test_decisions_are_journalled_and_explainable(ctx: ToolContext, clean_company: CompanyFacts) -> None:
    seeded = _seeded_context(ctx, clean_company, Decimal("12"))
    recorded = call_tool("record_decision", {"ticker": clean_company.ticker, "request_id": "d1"}, seeded)
    assert recorded.ok
    did = recorded.data["decision_id"]

    explained = call_tool("explain_decision", {"decision_id": did}, seeded)
    assert explained.ok
    assert explained.data["ticker"] == clean_company.ticker
    assert explained.data["falsification_condition"]
    assert explained.data["policy_version"] == seeded.policy.version

    history = call_tool("get_decision_history", {"ticker": clean_company.ticker}, seeded)
    assert history.data["count"] == 1


def test_recording_the_same_decision_twice_does_not_duplicate_it(
    ctx: ToolContext, clean_company: CompanyFacts
) -> None:
    seeded = _seeded_context(ctx, clean_company, Decimal("12"))
    call_tool("record_decision", {"ticker": clean_company.ticker, "request_id": "d1"}, seeded)
    call_tool("record_decision", {"ticker": clean_company.ticker, "request_id": "d2"}, seeded)
    assert len(seeded.decisions.read_all()) == 1


def test_full_review_says_nothing_to_do_when_there_is_nothing_to_do(ctx: ToolContext) -> None:
    result = _call(ctx, "run_full_review")
    assert result.ok
    assert result.data["holdings_reviewed"] == 0


def test_full_review_flags_holdings_it_could_not_check(ctx: ToolContext) -> None:
    _seed_position(ctx)
    result = _call(ctx, "run_full_review")
    assert result.data["no_data"] == ["AAAA"]


def test_compliance_alerts_are_quiet_with_no_holdings(ctx: ToolContext) -> None:
    result = _call(ctx, "get_compliance_alerts")
    assert result.data["alerts"] == []
    assert "No compliance alerts" in result.message


# ----------------------------------------------------------------------
# MCP envelope
# ----------------------------------------------------------------------
def test_initialize_and_list(ctx: ToolContext) -> None:
    init = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, lambda: ctx)
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "egx-shariah-engine"

    listed = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, lambda: ctx)
    assert listed is not None
    assert len(listed["result"]["tools"]) == len(REGISTRY)


def test_call_through_the_envelope_returns_json_content(ctx: ToolContext) -> None:
    response = handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_portfolio", "arguments": {}}},
        lambda: ctx,
    )
    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert response["result"]["isError"] is False


def test_a_refusal_is_content_not_a_protocol_error(ctx: ToolContext) -> None:
    response = handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "run_screening", "arguments": {}}},
        lambda: ctx,
    )
    assert response is not None
    assert "error" not in response
    assert response["result"]["isError"] is True


def test_notifications_get_no_response_and_unknown_methods_do(ctx: ToolContext) -> None:
    assert handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, lambda: ctx) is None
    unknown = handle_request({"jsonrpc": "2.0", "id": 5, "method": "resources/list"}, lambda: ctx)
    assert unknown is not None
    assert unknown["error"]["code"] == -32601


def test_bad_arguments_shape_is_a_protocol_error(ctx: ToolContext) -> None:
    response = handle_request(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "get_portfolio", "arguments": "none"}},
        lambda: ctx,
    )
    assert response is not None
    assert response["error"]["code"] == -32602


# ----------------------------------------------------------------------
# The conversation, end to end
# ----------------------------------------------------------------------
def test_the_day_one_conversation_runs_on_real_files(ctx: ToolContext) -> None:
    """hire -> contribute -> propose -> confirm -> 'what do I own?'"""
    assert _call(ctx, "record_contribution", amount="25000", request_id="c1", verbatim="I added 25,000").ok

    ctx.orders.propose(
        order_id="AAAA-1", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("500"), limit_price=Decimal("12.10"), validity=Validity.DAY,
        note="target weight 0.225",
    )
    assert _call(ctx, "get_order_proposals").data["count"] == 1

    fill = _call(
        ctx, "confirm_order_fill", order_id="AAAA-1", shares="500", price="12.00", fees="30",
        request_id="f1", verbatim="done, bought 500 at 12.00",
    )
    assert fill.ok

    priced = ToolContext(**{**ctx.__dict__, "prices": {"AAAA": Decimal("12.50")}})
    portfolio = _call(priced, "get_portfolio")
    assert portfolio.data["holdings"][0]["shares"] == "500"
    assert Decimal(portfolio.data["cash"]) == Decimal("18970")       # 25000 - 6000 - 30
    assert Decimal(portfolio.data["total_value"]) == Decimal("25220")  # + 500 x 12.50

    # Every one of those calls left a trace (R5).
    assert len(ctx.audit.read_all()) >= 4
    assert ctx.ledger.path.exists() and ctx.orders.path.exists()
