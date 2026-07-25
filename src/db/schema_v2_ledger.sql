-- ============================================================
-- MIGRATION 002 — PORTFOLIO LEDGER, ORDER LIFECYCLE, POLICY
-- Applies on top of schema.sql (migration 001).
--
-- ARCHITECTURE_V2 §6 (ledger), §12.1 (IPS), §12.2 (corporate
-- actions), §12.3 (auditing the reasoning plane).
--
-- Design principles carried forward from schema.sql:
--   1. Append-only. Portfolio state is a FOLD over events, never a
--      mutable balance. There is deliberately no `cash` column
--      anywhere — a stored balance can drift from its history.
--   2. All money is NUMERIC. Never float.
--   3. Every row that shapes a decision records the config and
--      policy versions in force when it was written.
-- ============================================================

-- ============================================================
-- ENUMS
-- ============================================================

CREATE TYPE ledger_event_type AS ENUM (
    'CONTRIBUTION',
    'WITHDRAWAL',
    'DIVIDEND_RECEIVED',
    'BUY_FILLED',
    'SELL_FILLED',
    'FEE_CHARGED',
    'SHARE_SPLIT',
    'BONUS_ISSUE',
    'PURIFICATION_ACCRUED',
    'PURIFICATION_SETTLED',
    'NOTE'
);

CREATE TYPE event_source AS ENUM ('USER_CONFIRMED','ENGINE','IMPORT');

CREATE TYPE order_status AS ENUM (
    'PROPOSED','FILLED','PARTIALLY_FILLED','EXPIRED','CANCELLED'
);

-- ============================================================
-- THE LEDGER  (APPEND-ONLY, the system of record for the portfolio)
-- ============================================================

CREATE TABLE ledger_events (
    seq             BIGSERIAL PRIMARY KEY,   -- fold order; strictly increasing
    event_type      ledger_event_type NOT NULL,
    occurred_at     DATE NOT NULL,           -- when it happened in the world
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    amount_egp      NUMERIC(24,2),           -- cash movements
    company_id      UUID REFERENCES companies(id),
    ticker          TEXT,                    -- denormalised for replay without joins
    shares          NUMERIC(24,4),           -- fractional only via corporate actions
    price           NUMERIC(18,4),
    fees            NUMERIC(18,4) NOT NULL DEFAULT 0,
    taxes           NUMERIC(18,4) NOT NULL DEFAULT 0,
    ratio           NUMERIC(18,8),           -- splits / bonus issues

    -- PROVENANCE of the instruction itself (§12.3). A money movement the
    -- user reported stores what they actually said, so a misheard fill is
    -- reconstructible rather than merely wrong.
    source          event_source NOT NULL DEFAULT 'USER_CONFIRMED',
    verbatim        TEXT,
    memo            TEXT,
    decision_id     UUID REFERENCES decisions(id),
    order_id        UUID,                    -- FK added after order_proposals

    CONSTRAINT amount_positive
        CHECK (amount_egp IS NULL OR amount_egp > 0),
    CONSTRAINT charges_non_negative
        CHECK (fees >= 0 AND taxes >= 0),
    CONSTRAINT ratio_positive
        CHECK (ratio IS NULL OR ratio > 0),

    -- Shape rules mirrored from engine.ledger.LedgerEvent.__post_init__ so
    -- that a malformed event cannot be inserted by any path, including SQL.
    CONSTRAINT cash_events_have_amount CHECK (
        event_type NOT IN ('CONTRIBUTION','WITHDRAWAL','DIVIDEND_RECEIVED',
                           'FEE_CHARGED','PURIFICATION_ACCRUED','PURIFICATION_SETTLED')
        OR amount_egp IS NOT NULL
    ),
    CONSTRAINT trades_are_complete CHECK (
        event_type NOT IN ('BUY_FILLED','SELL_FILLED')
        OR (ticker IS NOT NULL AND shares IS NOT NULL AND price IS NOT NULL
            AND shares > 0 AND price > 0)
    ),
    CONSTRAINT corporate_actions_have_ratio CHECK (
        event_type NOT IN ('SHARE_SPLIT','BONUS_ISSUE')
        OR (ticker IS NOT NULL AND ratio IS NOT NULL)
    )
);

CREATE INDEX idx_ledger_ticker ON ledger_events(ticker) WHERE ticker IS NOT NULL;
CREATE INDEX idx_ledger_occurred ON ledger_events(occurred_at);

-- ============================================================
-- ORDER PROPOSALS  (the propose -> confirm loop)
-- ============================================================
-- The agent writes a PROPOSED row. A proposal is NOT a position: only a
-- confirmed fill produces a ledger event. An ignored proposal expires,
-- which is why doing nothing is always safe for the user.

CREATE TABLE order_proposals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id       UUID REFERENCES reviews(id),
    decision_id     UUID REFERENCES decisions(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    ticker          TEXT NOT NULL,

    side            TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    order_type      TEXT NOT NULL DEFAULT 'LIMIT'
                    CHECK (order_type = 'LIMIT'),   -- R6: limit only, never market
    quantity        NUMERIC(24,0) NOT NULL CHECK (quantity > 0),
    limit_price     NUMERIC(18,4) NOT NULL CHECK (limit_price > 0),
    validity        TEXT NOT NULL DEFAULT 'DAY' CHECK (validity IN ('DAY','GTC')),

    status          order_status NOT NULL DEFAULT 'PROPOSED',
    proposed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      DATE NOT NULL,
    resolved_at     TIMESTAMPTZ,

    filled_quantity NUMERIC(24,0) NOT NULL DEFAULT 0
                    CHECK (filled_quantity >= 0),
    rationale       TEXT NOT NULL,
    threshold_version TEXT NOT NULL,
    policy_version    TEXT NOT NULL,

    CONSTRAINT fill_within_quantity CHECK (filled_quantity <= quantity),
    CONSTRAINT rationale_substantive CHECK (LENGTH(TRIM(rationale)) >= 30)
);

ALTER TABLE ledger_events
    ADD CONSTRAINT ledger_order_fk
    FOREIGN KEY (order_id) REFERENCES order_proposals(id);

CREATE INDEX idx_orders_open ON order_proposals(status)
    WHERE status IN ('PROPOSED','PARTIALLY_FILLED');

-- Order status may advance, but never backwards and never out of a
-- terminal state. Mirrors engine.ledger._ALLOWED_TRANSITIONS.
CREATE OR REPLACE FUNCTION guard_order_transition() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;
    IF OLD.status IN ('FILLED','EXPIRED','CANCELLED') THEN
        RAISE EXCEPTION 'order % is terminal (%) and cannot become %',
            OLD.id, OLD.status, NEW.status;
    END IF;
    IF OLD.status = 'PARTIALLY_FILLED' AND NEW.status = 'PROPOSED' THEN
        RAISE EXCEPTION 'order % cannot return to PROPOSED once partially filled', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER order_transition_guard BEFORE UPDATE ON order_proposals
    FOR EACH ROW EXECUTE FUNCTION guard_order_transition();

-- ============================================================
-- INVESTMENT POLICY STATEMENT  (APPEND-ONLY, §12.1)
-- ============================================================
-- The mandate the CIO operates under. Versioned exactly like
-- config/thresholds.yaml: never edited, only superseded. Every decision
-- records the policy_version in force so that in two years you can say
-- "we bought this under the policy you had then".

CREATE TABLE policy_versions (
    policy_version   TEXT PRIMARY KEY,        -- '1.0.0'
    effective_from   DATE NOT NULL,
    objective        TEXT NOT NULL
                     CHECK (objective IN ('LONG_TERM_GROWTH','INCOME','CAPITAL_PRESERVATION')),
    horizon_years    INT NOT NULL CHECK (horizon_years > 0),
    base_currency    TEXT NOT NULL DEFAULT 'EGP',

    contribution_amount   NUMERIC(18,2) CHECK (contribution_amount IS NULL OR contribution_amount > 0),
    contribution_cadence  TEXT NOT NULL DEFAULT 'NONE'
                          CHECK (contribution_cadence IN ('MONTHLY','IRREGULAR','NONE')),
    liquidity_reserve_egp NUMERIC(18,2) NOT NULL DEFAULT 0
                          CHECK (liquidity_reserve_egp >= 0),

    max_drawdown_tolerance NUMERIC(6,4)
                           CHECK (max_drawdown_tolerance IS NULL
                                  OR (max_drawdown_tolerance > 0 AND max_drawdown_tolerance <= 1)),

    -- Personal exclusions LAYER ON TOP of the Shariah gate. There is no
    -- column for inclusions by design: policy can never loosen the gate (R7).
    excluded_sectors  TEXT[] NOT NULL DEFAULT '{}',
    excluded_tickers  TEXT[] NOT NULL DEFAULT '{}',

    purification_basis TEXT NOT NULL DEFAULT 'DIVIDENDS'
                       CHECK (purification_basis IN ('DIVIDENDS','TOTAL_RETURN')),
    purification_cadence TEXT NOT NULL DEFAULT 'ANNUAL',
    review_cadence     TEXT NOT NULL DEFAULT 'QUARTERLY',
    digest_cadence     TEXT NOT NULL DEFAULT 'WEEKLY',

    adopted_reason   TEXT NOT NULL,           -- why the user changed it
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT adoption_reason_substantive CHECK (LENGTH(TRIM(adopted_reason)) >= 10)
);

-- Decisions and screening rows must be attributable to a policy version.
ALTER TABLE decisions ADD COLUMN policy_version TEXT REFERENCES policy_versions(policy_version);

-- ============================================================
-- AGENT ACTION LOG  (§12.3 — auditing the reasoning plane)
-- ============================================================
-- R5 makes the DATA auditable. For an LLM-fronted system that is not
-- enough: you must be able to prove no number the agent stated was
-- invented. Every tool call is logged with its arguments and a digest of
-- its result, so any figure in any message traces back to a tool result.

CREATE TABLE agent_tool_calls (
    id            BIGSERIAL PRIMARY KEY,
    run_id        UUID NOT NULL,             -- conversation turn or routine run
    run_kind      TEXT NOT NULL              -- 'CONVERSATION' | 'ROUTINE'
                  CHECK (run_kind IN ('CONVERSATION','ROUTINE')),
    routine_name  TEXT,
    tool_name     TEXT NOT NULL,
    arguments     JSONB NOT NULL,
    result_digest TEXT NOT NULL,             -- sha256 of the serialised result
    result_json   JSONB,                     -- full result where size permits
    succeeded     BOOLEAN NOT NULL,
    error_message TEXT,
    called_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms   INT
);

CREATE INDEX idx_tool_calls_run ON agent_tool_calls(run_id);
CREATE INDEX idx_tool_calls_failed ON agent_tool_calls(called_at) WHERE succeeded = FALSE;

-- Routine runs capture `now` ONCE at their boundary (§12.4) so a replay
-- reproduces the run exactly.
CREATE TABLE routine_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    routine_name  TEXT NOT NULL,
    clock_now     TIMESTAMPTZ NOT NULL,      -- the single authoritative "now"
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    events_written INT NOT NULL DEFAULT 0,
    message_sent  BOOLEAN NOT NULL DEFAULT FALSE,
    summary       TEXT,
    error_message TEXT
);

CREATE INDEX idx_routine_runs_recent ON routine_runs(routine_name, started_at DESC);

-- ============================================================
-- APPEND-ONLY ENFORCEMENT (reuses reject_mutation from schema.sql)
-- ============================================================

CREATE TRIGGER no_update_ledger BEFORE UPDATE OR DELETE ON ledger_events
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_policy BEFORE UPDATE OR DELETE ON policy_versions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_tool_calls BEFORE UPDATE OR DELETE ON agent_tool_calls
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- ============================================================
-- DERIVED VIEWS  (state is a FOLD — these read, never store)
-- ============================================================

-- Cash: a running fold, expressed in SQL. Deliberately a view: there is
-- no cash column to drift.
CREATE VIEW v_cash_balance AS
SELECT COALESCE(SUM(
    CASE event_type
        WHEN 'CONTRIBUTION'          THEN amount_egp
        WHEN 'DIVIDEND_RECEIVED'     THEN amount_egp
        WHEN 'WITHDRAWAL'            THEN -amount_egp
        WHEN 'FEE_CHARGED'           THEN -amount_egp
        WHEN 'PURIFICATION_SETTLED'  THEN -amount_egp
        WHEN 'BUY_FILLED'            THEN -(shares * price + fees + taxes)
        WHEN 'SELL_FILLED'           THEN  (shares * price - fees - taxes)
        ELSE 0
    END
), 0)::NUMERIC(24,2) AS cash_egp
FROM ledger_events;

-- Share counts, including corporate-action scaling. Ordering matters:
-- a split multiplies everything held at that point, so this view is a
-- convenience for reporting; engine.ledger.fold() is the authority.
CREATE VIEW v_holdings AS
SELECT ticker,
       SUM(CASE event_type
               WHEN 'BUY_FILLED'  THEN shares
               WHEN 'SELL_FILLED' THEN -shares
               ELSE 0
           END) AS net_shares_before_actions,
       COUNT(*) FILTER (WHERE event_type IN ('SHARE_SPLIT','BONUS_ISSUE')) AS corporate_actions
FROM ledger_events
WHERE ticker IS NOT NULL
GROUP BY ticker;

CREATE VIEW v_purification_balance AS
SELECT
    COALESCE(SUM(amount_egp) FILTER (WHERE event_type = 'PURIFICATION_ACCRUED'), 0)
  - COALESCE(SUM(amount_egp) FILTER (WHERE event_type = 'PURIFICATION_SETTLED'), 0)
    AS amount_due_egp
FROM ledger_events;

CREATE VIEW v_open_orders AS
SELECT o.*, c.egx_code, c.name_en
FROM order_proposals o
JOIN companies c ON c.id = o.company_id
WHERE o.status IN ('PROPOSED','PARTIALLY_FILLED');

CREATE VIEW v_current_policy AS
SELECT * FROM policy_versions
ORDER BY effective_from DESC, created_at DESC
LIMIT 1;
