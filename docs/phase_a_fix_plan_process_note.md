# Phase A Fix Plan — Process Note (§7)

This note records process-level evidence that is not inferable from source code alone.

## What can be validated from code/tests

- Strict YAML schema is active and `schema_version: 1` is present in root `config.yaml`.
- Metric contracts, OOS force-flat, disabled worst-segment semantics, ok-only markers,
  tier wording/disclaimers, bucket matrix visibility, and regression guards are covered
  by `wf_grid/tests`.
- Full test suite status at close-out: `1227 passed, 1 warning, 0 failed`.

## What must be validated in git/CI metadata

- Commit ordering requirements from plan §7 (A/B/C sequencing).
- Whether golden baseline updates were required as a separate commit.
- CI timing evidence (which checks ran before merge).

Source of truth for these items is PR history / CI logs, not repository file contents.
