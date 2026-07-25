# Recipe — the user tells you something happened

Triggers: "I added 25,000", "I got dividends", "done, bought 190 at 52.10",
"I sold half my ABUK".

## Steps

1. **Parse, do not assume.** Extract amount, ticker, shares, price, fees.
2. **Read it back and get confirmation before recording.** Money movements are
   confirmed, never inferred:
   > "Recording: bought 190 COMI at 52.10, fees 20 EGP — total 9,919 EGP. Correct?"
3. On confirmation, append **one** event:
   ```python
   from store.jsonl_ledger import JsonlLedgerStore
   from engine.ledger import LedgerEvent, EventType, EventSource
   store = JsonlLedgerStore("memory/portfolio/ledger.jsonl")
   store.append(LedgerEvent(
       seq=1,                       # ignored; the store assigns it
       event_type=EventType.BUY_FILLED,
       occurred_at="2026-07-25",    # the date it happened, not today
       ticker="COMI", shares=Decimal("190"), price=Decimal("52.10"),
       fees=Decimal("20"),
       source=EventSource.USER_CONFIRMED,
       verbatim="done, bought 190 at 52.10",   # what they actually said
   ))
   ```
4. Report the new state in one line: "Recorded. Cash is now 15,081 EGP, you hold
   190 COMI."
5. `git commit` the ledger — it is the memory.

## Refusals

- `InsufficientCashError` → tell them plainly: "That buy needs 9,919 EGP but the
  ledger shows 100 EGP. Did a contribution not get recorded?" **Never** force it.
- `InsufficientSharesError` → same. The ledger is probably behind reality; find the
  missing event rather than overriding.
- Never edit a past line to fix a mistake. Append a correcting event.
