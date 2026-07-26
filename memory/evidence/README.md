# memory/evidence/ — the source registry

Every document any stored number can be traced to lives here, beside a
`metadata.yaml` that describes it. `CLAUDE.md` R1 says provenance or nothing;
this directory is where "provenance" is a file you can open rather than a
sentence someone wrote.

---

## Layout

One directory per **evidence item** — not per source, and not per publisher:

```
memory/evidence/<publisher>/<series>/<as_of>/
    <the file>
    metadata.yaml
```

```
memory/evidence/egx/shariah_index/2026-04-30/
    constituents.xlsx
    metadata.yaml
```

The `as_of` segment is what makes this work over time. The index rebalances, the
CBE publishes monthly, companies file quarterly — a layout keyed only by source
would force every new vintage to overwrite the last, and overwriting evidence is
how an audit trail dies. Each vintage is a new directory; nothing is ever
replaced in place (R5).

Directories are cheap. Ambiguity about which bytes produced a verdict is not.

---

## The metadata schema

| Field | Meaning |
|---|---|
| `id` | Path-shaped identifier, matches the directory. Referenced from `config/`. |
| `version` | Bumped if this record's *description* is corrected. The bytes never change. |
| `title`, `publisher`, `kind` | What it is and who issued it. |
| `file`, `media_type`, `bytes`, `sha256` | The bytes, identified exactly. |
| `as_of` | What the document is **about**. |
| `acquired.at` / `.via` / `.original_filename` | How it reached us. |
| `acquired.url` / `.url_verified` | Where from — **null unless actually fetched**. |
| `used_by` | Every downstream file that depends on these bytes. |
| `supersedes` / `superseded_by` | The chain across vintages. |
| `notes` | Anything a future reader needs that the fields cannot hold. |

`sha256` lives here and **only** here. There is deliberately no `sha256.txt`
sidecar: a hash recorded in two places is a hash that can disagree with itself,
and the moment it does, neither copy is trustworthy. What replaces the sidecar is
better than a sidecar — `store/evidence.py` recomputes the digest from the actual
bytes, and `tests/test_evidence.py` runs that over every record in this tree on
every test run. The hash is checked continuously against reality rather than
transcribed twice and checked never.

---

## Admitting a document (the rule, not the ritual)

The checklist is `checklists/NEW_EVIDENCE.md`. The rule behind it:

> Record what you can verify. Leave null what you cannot. Never write a field
> because it looks incomplete empty.

`acquired.url` is the field this exists for. A URL that was not fetched is a
guess formatted as a citation — it makes the record *look* stronger while making
it *less* true, and a future reader has no way to tell the difference. `null`
plus `url_verified: false` is a smaller claim that happens to be a correct one.

The same applies to any field: `as_of` is parsed out of the document, never
inferred from the filename; `publisher` is what the document says it is.

---

## What does not belong here

- Anything derived. Transcriptions live in `config/`, results in `memory/`.
  Evidence is what we were *given*; everything else is what we *computed*.
- Large binaries whose bytes cannot be committed. `.gitignore` excludes filing
  PDFs; their `metadata.yaml` is still committed, so the provenance chain
  survives even where the bytes do not (`decisions/0002`).
- Anything unsourced. If it arrived without a way to say where it came from, it
  is not evidence — it is a rumour, and it stays out.
