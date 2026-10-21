# `.cairn/` spec 1.0 conformance suite

> Status: **skeleton (v0.5)**. Reference implementation only. External
> implementations may run this suite to self-declare compatibility once it
> stabilizes in v0.6.

## What this is

A small, fixture-driven pytest module that exercises the invariants of the
`.cairn/` folder format. It is intentionally
implementation-agnostic: every check reads or writes via the spec
contract, not via Cairn's internal API.

If your alternative tool reads or writes a `.cairn/` folder, you can fork
the checks below and run them against your implementation. If they pass,
you can claim "spec 1.0 read-compatible" or "spec 1.0 round-trip
compatible" as applicable.

## What's covered today (skeleton)

- Folder layout: minimum required files exist (§ top-level).
- `config.yaml`: required keys present, `format_version` parsable.
- `persistence/memory.db`: schema matches spec 1.0 (§4); decision columns
  present; `memory_evidence` table present; active-row filter
  (`valid_until IS NULL`) returns expected rows.
- Reader semantics for decisions: walking the `superseded_by` chain
  reaches the active head; `decision_audit_score` is derivable from the
  raw columns.
- `session-handoff.md`: spec 1.0 headers (`## Continuation Point`, `## In-Progress Tasks`,
  `## Completed This Session`, `## Summary`) are recognized.

## What's planned for v0.6

- Round-trip preservation of unknown top-level config keys.
- ChromaDB hybrid backend (§4) checks.
- Performance budget assertions (decay benchmark, recall@5).
- A signed compatibility badge generator.

## Running

```bash
python -m pytest tests/conformance/ -q
```

This suite ships alongside the reference implementation, so it MUST pass
on every Cairn release. A failure here means either the code drifted from
the spec, or the spec drifted from the code, neither is acceptable.
