-- ============================================================
-- EGX SHARIAH ENGINE — DATABASE SCHEMA
-- PostgreSQL 15+ / Supabase
--
-- Design principles:
--   1. Provenance is NOT NULL. A figure without a source cannot exist.
--   2. Screening, scoring and decision tables are APPEND-ONLY.
--   3. All money is NUMERIC. Never float.
--   4. Every computed row stores its input snapshot for reconstruction.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- ENUMS
-- ============================================================

CREATE TYPE period_type      AS ENUM ('Q1','H1','9M','FY');
CREATE TYPE audit_status     AS ENUM ('AUDITED','REVIEWED','UNAUDITED');
CREATE TYPE data_status      AS ENUM ('PENDING','VALIDATED','INSUFFICIENT','CONFLICT');
CREATE TYPE shariah_status   AS ENUM ('GREEN','AMBER','ORANGE','RED','DATA_INSUFFICIENT');
CREATE TYPE breach_type      AS ENUM ('NONE','TYPE_1_ACTIVITY','TYPE_2_STRUCTURAL','TYPE_3_DENOMINATOR','TYPE_4_DATA');
CREATE TYPE decision_type    AS ENUM ('BUY','ADD','HOLD','HOLD_FROZEN','REDUCE','SELL','REMOVE','NO_ACTION');
CREATE TYPE watchlist_name   AS ENUM ('HIGH_CONVICTION','BUY_ON_PULLBACK','HOLD','REMOVE');
CREATE TYPE statement_type   AS ENUM ('BALANCE_SHEET','INCOME_STATEMENT','CASH_FLOW','EQUITY','NOTE');
CREATE TYPE unit_scale       AS ENUM ('UNITS','THOUSANDS','MILLIONS');

-- ============================================================
-- REFERENCE
-- ============================================================

CREATE TABLE companies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    egx_code        TEXT NOT NULL UNIQUE,
    isin            TEXT UNIQUE,
    name_en         TEXT NOT NULL,
    name_ar         TEXT,
    sector          TEXT NOT NULL,
    sub_sector      TEXT,
    listing_date    DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    ir_url          TEXT,
    filings_url     TEXT,
    fiscal_year_end TEXT NOT NULL DEFAULT '12-31',  -- MM-DD; some EGX issuers use 06-30
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Where each company's filings are actually found. Built once, maintained.
CREATE TABLE filing_sources (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID NOT NULL REFERENCES companies(id),
    source_type       TEXT NOT NULL,   -- 'EGX_DISCLOSURE' | 'COMPANY_IR' | 'FRA'
    url_pattern       TEXT NOT NULL,
    discovery_method  TEXT NOT NULL,   -- 'RSS' | 'HTML_SCRAPE' | 'DIRECTORY_LISTING'
    selector          TEXT,
    priority          INT  NOT NULL DEFAULT 1,
    last_checked_at   TIMESTAMPTZ,
    last_success_at   TIMESTAMPTZ,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE
);

-- ============================================================
-- FILINGS AND EXTRACTION
-- ============================================================

CREATE TABLE filings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID NOT NULL REFERENCES companies(id),
    fiscal_year       INT  NOT NULL,
    period            period_type NOT NULL,
    period_end        DATE NOT NULL,
    audit_status      audit_status NOT NULL,
    filed_date        DATE,
    discovered_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_url        TEXT NOT NULL,
    file_sha256       TEXT NOT NULL UNIQUE,     -- idempotency key
    storage_path      TEXT NOT NULL,
    page_count        INT  NOT NULL,
    is_scanned        BOOLEAN NOT NULL,
    language          TEXT NOT NULL,            -- 'ar' | 'en' | 'ar+en'
    reported_scale    unit_scale NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'EGP',
    data_status       data_status NOT NULL DEFAULT 'PENDING',
    status_reason     TEXT,
    UNIQUE (company_id, fiscal_year, period)
);

CREATE INDEX idx_filings_company_period ON filings(company_id, fiscal_year DESC, period);
CREATE INDEX idx_filings_status ON filings(data_status) WHERE data_status <> 'VALIDATED';

-- One row per independent extraction pass (minimum two per filing)
CREATE TABLE extraction_runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filing_id      UUID NOT NULL REFERENCES filings(id),
    pass_number    INT  NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    raw_response   JSONB NOT NULL,
    token_usage    JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (filing_id, pass_number)
);

-- THE CORE TABLE. Provenance columns are NOT NULL by design.
CREATE TABLE line_items (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filing_id        UUID NOT NULL REFERENCES filings(id),
    statement        statement_type NOT NULL,
    item_key         TEXT NOT NULL,             -- canonical key from config/line_items.yaml
    value_egp        NUMERIC(24,2) NOT NULL,    -- normalised to EGP units
    raw_value        TEXT NOT NULL,             -- exactly as it appeared in the document
    raw_caption      TEXT NOT NULL,             -- the label as printed, AR or EN

    -- PROVENANCE — mandatory, no exceptions
    page_no          INT  NOT NULL,
    note_ref         TEXT,
    extraction_run_id UUID NOT NULL REFERENCES extraction_runs(id),

    is_derived       BOOLEAN NOT NULL DEFAULT FALSE,  -- e.g. Q2 = H1 - Q1
    derivation       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT page_no_positive CHECK (page_no > 0),
    CONSTRAINT derived_has_explanation
        CHECK (is_derived = FALSE OR derivation IS NOT NULL),
    UNIQUE (filing_id, item_key, extraction_run_id)
);

CREATE INDEX idx_line_items_filing_key ON line_items(filing_id, item_key);

-- Reconciled values after dual-extraction agreement (V9)
CREATE TABLE reconciled_values (
    filing_id     UUID NOT NULL REFERENCES filings(id),
    item_key      TEXT NOT NULL,
    value_egp     NUMERIC(24,2) NOT NULL,
    agreement     BOOLEAN NOT NULL,
    pass_1_value  NUMERIC(24,2),
    pass_2_value  NUMERIC(24,2),
    page_no       INT NOT NULL,
    note_ref      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (filing_id, item_key)
);

CREATE TABLE validations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filing_id    UUID NOT NULL REFERENCES filings(id),
    check_code   TEXT NOT NULL,        -- V1..V9
    check_name   TEXT NOT NULL,
    is_critical  BOOLEAN NOT NULL,
    passed       BOOLEAN NOT NULL,
    expected     NUMERIC(24,4),
    actual       NUMERIC(24,4),
    tolerance    NUMERIC(10,6),
    message      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_validations_failed ON validations(filing_id) WHERE passed = FALSE;

-- ============================================================
-- MARKET AND MACRO
-- ============================================================

CREATE TABLE market_data (
    company_id      UUID NOT NULL REFERENCES companies(id),
    as_of           DATE NOT NULL,
    close_price     NUMERIC(18,4) NOT NULL,
    shares_out      NUMERIC(24,0) NOT NULL,
    market_cap      NUMERIC(24,2) NOT NULL,
    volume          NUMERIC(24,0),
    turnover_egp    NUMERIC(24,2),
    adtv_60d        NUMERIC(24,2),
    mcap_avg_12m    NUMERIC(24,2),      -- screening denominator
    ma_50           NUMERIC(18,4),
    ma_200          NUMERIC(18,4),
    rsi_14          NUMERIC(6,2),
    source          TEXT NOT NULL,
    PRIMARY KEY (company_id, as_of)
);

CREATE TABLE macro_data (
    as_of              DATE PRIMARY KEY,
    cbe_deposit_rate   NUMERIC(6,3),
    cbe_lending_rate   NUMERIC(6,3),
    cbe_discount_rate  NUMERIC(6,3),
    tbill_1y_yield     NUMERIC(6,3),   -- used for ERP in Pillar 4
    cpi_yoy            NUMERIC(6,3),   -- used to deflate Pillar 5
    usd_egp            NUMERIC(10,4),
    egx30_close        NUMERIC(18,4),
    source             TEXT NOT NULL,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- SHARIAH SCREENING  (APPEND-ONLY)
-- ============================================================

CREATE TABLE shariah_screens (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id     UUID NOT NULL REFERENCES companies(id),
    filing_id      UUID NOT NULL REFERENCES filings(id),
    standard       TEXT NOT NULL,          -- 'AAOIFI' | 'MSCI' | 'DJIM' | ...
    screen_code    TEXT NOT NULL,          -- 'A' | 'B' | 'C' | 'D' | 'E'
    screen_name    TEXT NOT NULL,
    numerator      NUMERIC(24,2) NOT NULL,
    denominator    NUMERIC(24,2) NOT NULL,
    ratio          NUMERIC(12,6) NOT NULL,
    threshold      NUMERIC(12,6) NOT NULL,
    utilisation    NUMERIC(12,6) NOT NULL, -- ratio / threshold
    status         shariah_status NOT NULL,
    inputs_json    JSONB NOT NULL,         -- full snapshot for reconstruction
    threshold_version TEXT NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT denominator_nonzero CHECK (denominator <> 0)
);

CREATE TABLE shariah_status_history (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id            UUID NOT NULL REFERENCES companies(id),
    filing_id             UUID NOT NULL REFERENCES filings(id),
    overall_status        shariah_status NOT NULL,
    worst_screen_code     TEXT,
    worst_utilisation     NUMERIC(12,6),
    breach                breach_type NOT NULL DEFAULT 'NONE',
    consecutive_compliant_quarters INT NOT NULL DEFAULT 0,
    cure_window_opened_at DATE,
    cure_window_expires_at DATE,
    -- Type 3 diagnostic: did numerator or denominator drive the change?
    numerator_delta_pct   NUMERIC(12,6),
    denominator_delta_pct NUMERIC(12,6),
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, filing_id)
);

-- ============================================================
-- SCORING  (APPEND-ONLY)
-- ============================================================

CREATE TABLE scores (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    UUID NOT NULL REFERENCES companies(id),
    filing_id     UUID NOT NULL REFERENCES filings(id),
    pillar        INT  NOT NULL CHECK (pillar BETWEEN 1 AND 7),
    subcriterion  TEXT NOT NULL,         -- '1A','2C','6B' ...
    points        NUMERIC(6,2) NOT NULL,
    points_max    NUMERIC(6,2) NOT NULL,
    metric_value  NUMERIC(24,6),
    band_matched  TEXT NOT NULL,
    inputs_json   JSONB NOT NULL,
    rationale     TEXT NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT points_in_range CHECK (points >= 0 AND points <= points_max),
    UNIQUE (company_id, filing_id, subcriterion)
);

CREATE TABLE score_totals (
    company_id     UUID NOT NULL REFERENCES companies(id),
    filing_id      UUID NOT NULL REFERENCES filings(id),
    p1_shariah     NUMERIC(6,2) NOT NULL,
    p2_financial   NUMERIC(6,2) NOT NULL,
    p3_earnings    NUMERIC(6,2) NOT NULL,
    p4_valuation   NUMERIC(6,2) NOT NULL,
    p5_growth      NUMERIC(6,2) NOT NULL,
    p6_technical   NUMERIC(6,2) NOT NULL,
    p7_governance  NUMERIC(6,2) NOT NULL,
    total          NUMERIC(6,2) NOT NULL,
    band           TEXT NOT NULL,
    rank_in_period INT,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, filing_id),
    CONSTRAINT total_bounded CHECK (total >= 0 AND total <= 100)
);

-- ============================================================
-- DECISIONS, WATCHLISTS, PORTFOLIO
-- ============================================================

CREATE TABLE reviews (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_code  TEXT NOT NULL UNIQUE,   -- 'R1-2026'
    fiscal_year  INT  NOT NULL,
    period       period_type NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    report_path  TEXT,
    status       TEXT NOT NULL DEFAULT 'RUNNING'
);

CREATE TABLE decisions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id               UUID NOT NULL REFERENCES reviews(id),
    company_id              UUID NOT NULL REFERENCES companies(id),
    filing_id               UUID REFERENCES filings(id),
    decision                decision_type NOT NULL,
    score                   NUMERIC(6,2),
    shariah_status          shariah_status NOT NULL,
    fair_value              NUMERIC(18,4),
    price_at_decision       NUMERIC(18,4),
    valuation_gap_pct       NUMERIC(12,6),
    trigger                 TEXT NOT NULL,
    reason                  TEXT NOT NULL,
    falsification_condition TEXT NOT NULL,
    veto_fired              TEXT,
    confidence              TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Reason and falsification must be substantive, not placeholders
    CONSTRAINT reason_substantive CHECK (LENGTH(TRIM(reason)) >= 30),
    CONSTRAINT falsification_substantive CHECK (LENGTH(TRIM(falsification_condition)) >= 20)
);

CREATE TABLE watchlist_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id),
    review_id   UUID NOT NULL REFERENCES reviews(id),
    list_name   watchlist_name NOT NULL,
    prev_list   watchlist_name,
    reason      TEXT NOT NULL,
    trigger_price NUMERIC(18,4),        -- for BUY_ON_PULLBACK
    quarters_on_list INT NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE positions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id   UUID NOT NULL REFERENCES companies(id),
    shares       NUMERIC(24,0) NOT NULL,
    avg_cost     NUMERIC(18,4) NOT NULL,
    opened_at    DATE NOT NULL,
    closed_at    DATE,
    is_open      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE transactions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    UUID NOT NULL REFERENCES companies(id),
    decision_id   UUID REFERENCES decisions(id),
    side          TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    shares        NUMERIC(24,0) NOT NULL,
    price         NUMERIC(18,4) NOT NULL,
    fees          NUMERIC(18,4) NOT NULL DEFAULT 0,
    taxes         NUMERIC(18,4) NOT NULL DEFAULT 0,
    traded_at     TIMESTAMPTZ NOT NULL,
    entered_by    TEXT NOT NULL DEFAULT 'MANUAL'
);

CREATE TABLE purification_ledger (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id         UUID NOT NULL REFERENCES companies(id),
    filing_id          UUID NOT NULL REFERENCES filings(id),
    impure_income      NUMERIC(24,2) NOT NULL,
    net_profit         NUMERIC(24,2) NOT NULL,
    purification_ratio NUMERIC(12,6) NOT NULL,
    dividends_received NUMERIC(18,2) NOT NULL DEFAULT 0,
    amount_due         NUMERIC(18,2) NOT NULL,
    settled_at         DATE,
    settlement_ref     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- AUDIT
-- ============================================================

CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    entity      TEXT NOT NULL,
    entity_id   UUID,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    payload     JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- APPEND-ONLY ENFORCEMENT
-- ============================================================

CREATE OR REPLACE FUNCTION reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
      'Table % is append-only. Insert a new row instead of modifying history.',
      TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER no_update_screens   BEFORE UPDATE OR DELETE ON shariah_screens
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_status    BEFORE UPDATE OR DELETE ON shariah_status_history
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_scores    BEFORE UPDATE OR DELETE ON scores
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_totals    BEFORE UPDATE OR DELETE ON score_totals
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_decisions BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_update_lineitems BEFORE UPDATE OR DELETE ON line_items
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- ============================================================
-- GUARD: a filing that failed validation can never be screened
-- ============================================================

CREATE OR REPLACE FUNCTION guard_validated_only() RETURNS TRIGGER AS $$
DECLARE st data_status;
BEGIN
    SELECT data_status INTO st FROM filings WHERE id = NEW.filing_id;
    IF st <> 'VALIDATED' THEN
        RAISE EXCEPTION
          'Filing % has data_status=% and cannot be screened or scored.',
          NEW.filing_id, st;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER guard_screens BEFORE INSERT ON shariah_screens
    FOR EACH ROW EXECUTE FUNCTION guard_validated_only();
CREATE TRIGGER guard_scores  BEFORE INSERT ON scores
    FOR EACH ROW EXECUTE FUNCTION guard_validated_only();

-- ============================================================
-- CONVENIENCE VIEWS
-- ============================================================

CREATE VIEW v_latest_status AS
SELECT DISTINCT ON (s.company_id)
       c.egx_code, c.name_en, s.company_id, s.overall_status, s.breach,
       s.worst_screen_code, s.worst_utilisation, t.total AS score, t.band,
       f.fiscal_year, f.period, f.period_end
FROM shariah_status_history s
JOIN companies c ON c.id = s.company_id
JOIN filings   f ON f.id = s.filing_id
LEFT JOIN score_totals t ON t.filing_id = s.filing_id AND t.company_id = s.company_id
ORDER BY s.company_id, f.period_end DESC;

CREATE VIEW v_exception_queue AS
SELECT f.id AS filing_id, c.egx_code, c.name_en, f.fiscal_year, f.period,
       f.data_status, f.status_reason,
       ARRAY_AGG(v.check_code) FILTER (WHERE v.passed = FALSE) AS failed_checks
FROM filings f
JOIN companies c ON c.id = f.company_id
LEFT JOIN validations v ON v.filing_id = f.id
WHERE f.data_status IN ('INSUFFICIENT','CONFLICT')
GROUP BY f.id, c.egx_code, c.name_en, f.fiscal_year, f.period,
         f.data_status, f.status_reason;
