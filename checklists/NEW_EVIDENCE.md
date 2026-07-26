# Checklist — admitting a new source document

Run this whenever a document arrives that any stored figure will trace back to:
an index export, a filing, a macro release, a price file.

Contract and schema: `memory/evidence/README.md`.

---

- [ ] **Pick the item directory** —
      `memory/evidence/<publisher>/<series>/<as_of>/`.
      `<as_of>` is what the document is *about*, read out of the document
      itself. If you cannot determine it from the document, stop — you do not
      know what period the data describes, and a mislabelled vintage silently
      corrupts every comparison against it.

- [ ] **Copy the file in unmodified.** Do not rename fields, re-save, convert,
      or "clean" it. Re-saving an `.xlsx` changes its bytes and destroys the
      only link back to what was actually received.

- [ ] **Hash it.**
      `uv run python -c "from pathlib import Path; from store.evidence import sha256_of; print(sha256_of(Path('<file>')))"`

- [ ] **Write `metadata.yaml`.** Every required field: `id`, `title`,
      `publisher`, `kind`, `file`, `sha256`, `as_of`.

- [ ] **Leave `acquired.url` null unless you actually fetched it.**
      Uploaded by the user → `via: USER_UPLOAD`, `url: null`,
      `url_verified: false`. A plausible URL you did not visit is not a
      citation; it is a guess wearing one. Say what you know.

- [ ] **Fill `used_by`** with every file that will depend on these bytes. A test
      asserts each path exists. This is the blast radius if the source is later
      corrected.

- [ ] **Link the chain** if this replaces an earlier vintage: set `supersedes`
      here and `superseded_by` on the old record. Never delete or overwrite the
      old one — evidence is append-only (R5), and a superseded source is still
      the correct explanation of decisions made while it was current.

- [ ] **Verify.** `uv run pytest tests/test_evidence.py -q`

- [ ] **Point the consumer at the id,** not at a path — e.g.
      `retrieved.evidence_id` in `config/universe.yaml`. If you also record the
      hash in the consumer, add a test that the two agree.

- [ ] **Commit the metadata even if the bytes are gitignored** (filing PDFs
      are). The provenance chain must survive where the bytes cannot
      (`decisions/0002`).

---

## Stop and ask instead of proceeding if

- The document contradicts a source already in evidence. Two disagreeing
  sources is a question for the user, not something to resolve by picking the
  more recent one.
- You cannot establish the publisher or the as-of date.
- The document arrived without any way to say where it came from. That is not
  evidence, and it does not get admitted.
