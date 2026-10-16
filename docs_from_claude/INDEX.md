# Design thread ledger

Every question (Q) and deviation (D) raised with the design thread, with its status. IDs are global and permanent.
Outbound files live in `docs_from_claude/`. Replies arrive in `docs_to_claude/direction/`.

| ID | Type | Summary | Raised in | Status | Answer |
| --- | --- | --- | --- | --- | --- |
| Q1 | question | Keep an Anthropic model (e.g. Sonnet 5.5) in a tier, given that the ranking rule leaves it out? Build recommends pinning it as the 4th balanced member | 2026-10-16-closeout-phase1-build.md | open | |
| Q2 | question | How to compare single-score (max-effort) AA models with models scored at medium effort; all 9 current tier members are single-score. Blocks ratifying D1 | 2026-10-16-closeout-phase1-build.md | open | |
| Q3 | question | Phase 1 shadow target project (design suggested the Video Intelligence Pipeline). Blocks plan step 5 and exit criteria 3-4 | 2026-10-16-closeout-phase1-build.md | open | |
| Q4 | question | Require at least one non-OpenRouter member per tier? 8 of 9 ranked cloud members are called through OpenRouter | 2026-10-16-closeout-phase1-build.md | open | |
| D1 | deviation | Quality floors 25/40/55 changed to 25/38/48 (price ceilings unchanged: $0.50 / $4.00 / none) | 2026-10-16-closeout-phase1-build.md | open | |
| D2 | deviation | Exclusive tiers: each model joins only its highest qualifying tier | 2026-10-16-closeout-phase1-build.md | open | |
| D3 | deviation | Score at medium effort when AA publishes it, and send matching reasoning_effort on calls | 2026-10-16-closeout-phase1-build.md | open | |
| D4 | deviation | Lenient name matching (date suffix, -0, -preview) plus snapshot de-dup in ranking | 2026-10-16-closeout-phase1-build.md | open | |
| D5 | deviation | Local web app (`tokenrouter serve`) added, although the design says Phase 1 has no UI | 2026-10-16-closeout-phase1-build.md | open | |
| D6 | deviation | Refreshes wait for `tokenrouter apply` approval (answers a design open question); the first refresh auto-applies | 2026-10-16-closeout-phase1-build.md | open | |
| D7 | deviation | No coding tier yet; coding_idx and agentic_idx stored (answers a design open question) | 2026-10-16-closeout-phase1-build.md | open | |
| D8 | deviation | Keys from the environment or a gitignored .env file, not the environment only | 2026-10-16-closeout-phase1-build.md | open | |
