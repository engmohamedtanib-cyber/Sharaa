# Checklist — the index rebalanced

The EGX 33 Shariah Index is reviewed periodically. When a new constituents
export arrives, the universe is **regenerated, never edited**.

---

- [ ] **Admit the export as evidence** — `checklists/NEW_EVIDENCE.md`. New
      vintage, new directory: `memory/evidence/egx/shariah_index/<as_of>/`.
      Set `supersedes` to the previous record's id, and `superseded_by` on the
      previous record.

- [ ] **Re-run the transcriber:**
      ```
      uv run --extra research python -m research.transcribers.transcribe_index \
          memory/evidence/egx/shariah_index/<as_of>/constituents.xlsx
      ```
      It stops rather than guesses if the column layout changed, if the as-of
      header is unreadable, or if the weights disagree with the workbook's own
      totals row.

- [ ] **Replace the `constituents:` block** in `config/universe.yaml` with the
      output. Do not merge by hand, and do not carry over rows from the old
      list because they "should still be there" — a company dropped by the
      index must disappear from the universe.

- [ ] **Update `retrieved`**: `evidence_id`, `from`, `at`, `sha256`,
      `weights_as_of`. Bump `version`.

- [ ] **Check the issuer count against the index name.** If distinct
      `issuer_id` values no longer equal `index.constituent_count`, the loader
      will refuse and it needs a human look — either a company was added or
      dropped (update the count), or a new dual listing appeared (add it to
      `DUAL_LISTINGS` in the transcriber, quoting the source rows as evidence).

- [ ] **Run the suite.** `uv run pytest -q`

- [ ] **Diff the tickers before and after,** and write the additions and
      removals into the commit message. This is the record of what changed in
      the halal universe and when.

---

## For every company that LEFT the index

Removal from the index is a Shariah-relevant event, not bookkeeping.

- [ ] Is it currently held? If so it does **not** auto-sell — run it through
      Screens A–E on its own filings and let `engine/decisions.py` decide.
      Index membership never was the verdict (`decisions/0004`).
- [ ] If it is held and now fails the gate, purification and the cure window
      apply (`engine/purification.py`, `engine/shariah.py`).
- [ ] Record the removal and what was done about it in the decision journal.

## For every company that JOINED

- [ ] It is in the universe, and that is all. It is not screened, not scored,
      not investable until its own filings have been ingested and validated.
      Membership selects candidates; it never substitutes for the gate (R7).
