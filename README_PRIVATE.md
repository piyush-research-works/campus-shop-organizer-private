# Campus Shop Challenge — Organizer Private

This repository contains the official execution system, final hidden seeds,
scoring code, and the Oracle reference agent. Never share it with participants.

## Contents

- `official_runner/` — frozen three-seed, 60-day execution
- `official_scoring/` — official score and leaderboard generation
- `oracle_agent/` — exact Anchor-First Oracle, batch runner, and QA adapter
- `docs/` — organizer procedure and security controls
- `tests/` — repository integrity checks

## Organizer QA

```powershell
python official_runner/run_private_evaluation.py qa `
  --days 2 `
  --participant-id oracle_qa `
  --manifest oracle_agent/runnable/agent_manifest.json
```

## Official participant run

```powershell
python official_runner/run_private_evaluation.py final `
  --participant-id team_a `
  --manifest student_submissions/team_a/agent_manifest.json
```

Repeat with the same frozen seed file for every team. Then create a scoring
manifest and run:

```powershell
python official_scoring/score_results.py `
  --manifest official_scoring/scoring_manifest.json
```

Read `docs/OFFICIAL_EVALUATION.md` before an event.

Run the exact Oracle separately with:

```powershell
python oracle_agent/run_oracle_batch.py final `
  --codex-command "C:\path\to\codex.exe"
```
