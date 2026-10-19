# Build handoff — 2026-10-16

Written at the end of build session 1 for the next build session. Overwrite it at the end of each
session (git keeps the history). Lasting project knowledge lives in `CLAUDE.md`.

## Where things stand

Phase 1 is **built and verified on live services**, and the build is **paused waiting for the design
thread**. The close-out `docs_from_claude/2026-10-16-closeout-phase1-build.md` raises Q1–Q4 and asks
the design thread to ratify D1–D8 (all `open` in `docs_from_claude/INDEX.md`). The user carries it to
the design thread; its reply will land in `docs_to_claude/direction/`.

## This session's commits

| Commit | What |
| --- | --- |
| 2b1b4b3 | Phase 1: catalog refresh, Jev decision, LiteLLM execution, decision/cost log, shadow mode, eval, report, audit |
| 08fc25d | Local web app (`tokenrouter serve`): playground with per-stage trace, tiers/catalog charts, request log |
| ad87a38 | CLI loads `.env`; AA free endpoint |
| b24ae87 | First real `config/tiers.yaml` tracked; `.env.*` ignored |
| 049d214 | Medium-effort scoring + `reasoning_effort` on calls, lenient name matching, exclusive tiers, bands 25/38/48, empty-answer fallback |
| 32940c5 | Close-out + ledger for the design thread; deviation IDs tagged in code/config |

## What the user said that isn't in code yet

- ~~Keep some Anthropic models, like Sonnet 5.5, in a tier.~~ **Wrong: misattributed.** Corrected
  2026-10-18. The user's words: "I feel that claude code is biased on using claude models even though
  model selector advised differently. So, I have asked design thread to keep the model selector decision
  intact". Q1 answered (d): leave out. See the CLAUDE.md neutrality rule.
- **Gemini 4 Argon and Muse Spark 1.3 (newer, single-score models) are probably overstated.** Confirmed
  broader: all 9 ranked tier members have only AA's plain (max-effort) score, so they're compared with
  Anthropic's medium-effort scores. This is ledger **Q2**.
- **Report to the design thread before building more features.** That's what produced the skill and the
  close-out.
- Still unanswered by the user: **which project is the shadow target** (Q3; the design suggests the Video
  Intelligence Pipeline agents, not on this machine as far as we know: ask for its path), and **who
  grades** the 100+ shadow pairs (by hand, or an LLM judge `tokenrouter eval judge` to be built, with
  ~20 spot-checks).

## Measured facts (2026-10-16)

- Refresh 2 (live): 344 OpenRouter models, 703 AA models, 166 matched, 178 unmatched, alias map empty.
- Live tiers (`config/tiers.yaml`): fast = deepseek-v4-flash, mimo-v2.6-flash, solar-pro4, + ollama
  qwen3:8b; balanced = ling-3.1-flash, deepseek-v4.1-flash, glm-5.3-flash; reasoning = muse-spark-1.3,
  gemini-4-argon (native Gemini key), muse-wonder. 8 of 9 cloud members go through OpenRouter.
- Each primary answered a live test call: fast $0.000013, balanced $0.000009, reasoning $0.0016.
- Jev decisions are 220–250 ms. Two real `ask` calls are logged in `data/decisions.db`.
- 61 tests pass.

## Next session: do this first

1. Onboard with `syncing-with-design-thread`: look for a reply in `docs_to_claude/direction/`, then
   mark answered items in the ledger.
2. **If there is a reply:** implement the answers (likely: a Sonnet pin or a provider rule, scoring for
   single-score models, maybe re-tuned bands), run `uv run tokenrouter refresh`, review the diff, and
   send it back in a short close-out before applying it if the reply asks for review.
3. **If there's no reply yet:** don't add features. Things not covered by open items are fine (bug
   fixes, tests, docs). Ask the user whether to proceed provisionally on anything else.
4. The shadow run (plan step 5) waits on Q3 and the project path; grading waits on the grader choice.

## Runtime state

- Web app: not running. Start it with `uv run tokenrouter serve`.
- `data/` (gitignored): `catalog.db` (refreshes 1–2), `decisions.db`, `playground.db`,
  `diffs/2026-10-16-refresh-{1,2}.md`, `tiers.previous.yaml` (refresh 1), `demo/` workspace.
- No launchd job is installed yet (`scripts/install_launchd.sh` does that); the first unattended
  refresh would be 1 November, 06:00.
- The skill lives at `~/.claude/skills/syncing-with-design-thread/` and is not backed up in any repo.
