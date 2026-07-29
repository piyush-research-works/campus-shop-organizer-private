# Oracle reference agent

The exact organizer benchmark is Blue V2.7 Anchor-First. Its complete tested
dependency chain is stored under `oracle_agent/historical_full_source/`.
`oracle_agent/run_oracle_batch.py` runs that source across the same three-seed
batch and emits a scorer-compatible summary. It uses only the same daily public
observation supplied to a participant agent. It does not read generated demand
or future events while deciding.

The historical changed-layer snapshot and experiment summary are retained
under `oracle_agent/reference_snapshot/`. A lightweight compatibility adapter
also exists under `oracle_agent/runnable/` for fast pipeline QA. That adapter is
not the scored Oracle benchmark.

- protect rent and an operating anchor
- use compact recent demand memory
- reserve cash before ordering
- favor durable, contribution-positive core stock
- reduce fresh exposure during weak demand
- consolidate a weak shop only when cash is threatened
- use at most one optional model call per day

The hackathon limit is 3,800 tokens per day. This is slightly tighter than the
historical 4,000-token experiment.

Official Oracle benchmark:

```powershell
python oracle_agent/run_oracle_batch.py final `
  --codex-command "C:\path\to\codex.exe"
```

Fallback-only mode is allowed for short pipeline QA, never for reporting
Oracle performance.
