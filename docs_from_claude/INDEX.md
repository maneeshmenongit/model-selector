# Design thread ledger

Every question (Q) and deviation (D) raised with the design thread, with its status. IDs are global and permanent.
Outbound files live in `docs_from_claude/`. Replies arrive in `docs_to_claude/direction/` or `docs_to_claude/base_architecture/phase N/`.

| ID | Type | Summary | Raised in | Status | Answer |
| --- | --- | --- | --- | --- | --- |
| Q1 | question | Keep an Anthropic model (e.g. Sonnet 5.5) in a tier, given that the ranking rule leaves it out? Build recommends pinning it as the 4th balanced member | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: (d) leave out; `extra_members` is for local models only |
| Q2 | question | How to compare single-score (max-effort) AA models with models scored at medium effort; all 9 current tier members are single-score. Blocks ratifying D1 | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: (c)+(d): median max→medium gap across all families, same for every provider, flag `effort_unverified`; then re-derive bands (N2) |
| Q3 | question | Phase 1 shadow target project (design suggested the Video Intelligence Pipeline). Blocks plan step 5 and exit criteria 3-4 | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: (a) Video Intelligence Pipeline; baseline = each agent's current model, recorded per agent in config |
| Q4 | question | Require at least one non-OpenRouter member per tier? 8 of 9 ranked cloud members are called through OpenRouter | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: (a) yes, ≥1 member per tier reachable without OpenRouter, chosen by the same ranking (N3) |
| D1 | deviation | Quality floors 25/40/55 changed to 25/38/48 (price ceilings unchanged: $0.50 / $4.00 / none) | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified provisionally; re-derive as p50/p75/p90 after Q2 (N2) |
| D2 | deviation | Exclusive tiers: each model joins only its highest qualifying tier | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified |
| D3 | deviation | Score at medium effort when AA publishes it, and send matching reasoning_effort on calls | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified |
| D4 | deviation | Lenient name matching (date suffix, -0, -preview) plus snapshot de-dup in ranking | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified |
| D5 | deviation | Local web app (`tokenrouter serve`) added, although the design says Phase 1 has no UI | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified, scope frozen: no further UI work until the shadow run reports |
| D6 | deviation | Refreshes wait for `tokenrouter apply` approval (answers a design open question); the first refresh auto-applies | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified |
| D7 | deviation | No coding tier yet; coding_idx and agentic_idx stored (answers a design open question) | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified |
| D8 | deviation | Keys from the environment or a gitignored .env file, not the environment only | 2026-10-16-closeout-phase1-build.md | answered | base_architecture/phase 2/tokenrouter-phase1b-next-steps.md: ratified |

## Direction items

Instructions from the design thread that answer no Q/D. Tracked here until reported in a close-out.

| ID | From | Summary | Status |
| --- | --- | --- | --- |
| N1 | phase1b §1, §4 | Provider-neutrality guard: CLAUDE.md rule, checks N-T1…N-T6 in pytest and `tokenrouter audit`, remove vendor-specific config. P0, blocks all other work | in progress |
| N2 | phase1b §4 | Effort normalization (Q2) and percentile bands (D1) | todo (after N1) |
| N3 | phase1b §4 | Non-OpenRouter fallback per tier (Q4) | todo |
| N4 | phase1b §4 | Alias the top 25 unmatched models | todo |
| N5 | phase1b §4 | Shadow run on the Video Intelligence Pipeline (Q3); needs the project path and a grader choice from the user | todo, blocked |
| N6 | phase1b §4 | Observe the 1 Nov 06:00 unattended refresh | todo (date-gated) |
| N7 | phase1b §4 | Live failure tests: Jev, OpenRouter, one direct provider down | todo |
| DC | phase1b §3 | Design corrections to `tokenrouter-design.md`: per-agent baseline, no vendor tier examples, N-T5 tie-break | todo (with N1) |
