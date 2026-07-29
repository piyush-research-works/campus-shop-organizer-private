# Official evaluation procedure

1. Freeze and hash every submitted agent.
2. Disable participant internet access.
3. Verify the final seed checksum.
4. Run every agent on the same three hidden seeds.
5. Run exactly 60 simulated days per seed.
6. Start each seed with empty agent memory.
7. Do not edit source code during evaluation.
8. Add the two human-review scores.
9. Generate the official leaderboard.
10. Archive the source hash, configuration hash, logs, and result report.

The runner creates a new output folder for every execution and never
overwrites earlier evidence.

The final simulator score is:

- 70% average score across three seeds
- 30% worst-seed score

The simulator contributes 90 points. Design/code quality and the final
explanation contribute 10 points.

