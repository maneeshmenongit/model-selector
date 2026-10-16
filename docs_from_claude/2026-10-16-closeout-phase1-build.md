---
type: closeout
date: 2026-10-16
phase: Phase 1, steps 1-4 built, step 5 tooling built (shadow run not started)
commits: 2b1b4b3..049d214
needs_direction: yes
answers: [base_architecture/tokenrouter-design.md]
---

# Phase 1 build close-out: tokenrouter

Answers "Token-Aware Model Router — Research, Design & Phase 1" (Oct 14, 2026). This is the build's first report back; there were no earlier messages in either direction.

## Bottom line

- Plan steps 1-4 (catalog refresh, LiteLLM execution, Jev decision, decision + cost log) are built and have run against real data. Step 5 (shadow run + grading) has its tooling built but has not started, so none of the cost or quality exit criteria is measured yet.
- Direction is needed on 4 questions (Q1-Q4) and 8 deviations (D1-D8) to ratify or reject.
- The most important decision is **Q2**. Artificial Analysis (AA) publishes scores per reasoning effort, and its plain score is maximum effort. The build moved to medium-effort scoring (D3) and retuned the bands on that basis (D1). But every model now in a tier has only a plain score, so the tiers probably compare max-effort scores against medium-effort ones. That is likely why no Anthropic model is in any tier's top 3 (Q1).
- Live checks: Jev decisions took about 220-250 ms; one routed fast-tier call cost $0.000013; 61 offline tests pass.

## Decisions needed

### Q1. Should an Anthropic model (for example Sonnet 5.5) be kept in a tier even when the ranking rule leaves it out?

The user wants Sonnet kept in a tier. Today no Anthropic model is in any tier's top 3. Sonnet 5.5 scores 40.8 at medium effort with a blended price of $4.00, which puts it in balanced (floor 38, ceiling $4.00). But its quality per dollar is 40.8 / 4.00 = 10.2, against 176.0 for balanced's #3 (GLM 5.3 Flash, 41.8 / $0.2375).

*Blocks:* nothing in the build. It affects provider diversity (see Q4) and which models the shadow run exercises.

- (a) **Pinned member.** Add a config list (like the existing `extra_members` used for local qwen3) that puts `anthropic/claude-sonnet-5-5` last in balanced, called directly with our Anthropic key. Trade-off: explicit and visible in the diff, and it only costs money when the cheaper members fail; but it is a hand override that the monthly refresh never re-checks.
- (b) **Provider preference in ranking.** Give providers we hold a key for a weight, rather than using them only to break ties. Trade-off: it applies to everyone, but against a 17x gap in quality per dollar (10.2 vs 176.0), any weight big enough to matter would distort the whole ranking.
- (c) **Score Anthropic at the effort we would call it at (high or max) and place it there.** At its plain (max-effort) score of 56.0, Sonnet's quality per dollar is 14.0. That would rank #2 in reasoning, ahead of Gemini 4 Argon (13.15). Trade-off: this only becomes fair once Q2 settles what effort the other models' scores reflect. Max-effort calls also use more reasoning tokens per request, which blended price does not capture.
- (d) **Leave it out.** Sonnet stays only as the shadow-run baseline. Trade-off: the purest ranking, but it goes against the user's stated wish.

*Build recommends:* (a), Sonnet as the 4th balanced member. It states a real preference honestly instead of bending the scores to get an outcome, and it also gives balanced a fallback outside OpenRouter (Q4). Revisit after Q2: under Q2 (b), Sonnet may earn a reasoning slot on merit.
*If no answer:* the build does not add Sonnet. The tiers stay as the ranking rule produces them.

### Q2. How should models that publish only one AA score be compared with models scored at medium effort?

AA's plain slug is the model at maximum effort: Sonnet 5.5 scores 56.0 there and 40.8 at medium (-15.2 points, -27%). The build now scores each model at medium effort when AA publishes a medium variant, and sends `reasoning_effort: medium` on calls (D3). But all 9 ranked members in today's tiers have only a plain AA slug, so each one is scored from that single number and called with no `reasoning_effort`. The user suspects Gemini 4 Argon and Muse Spark 1.3 are inflated this way.

Illustration only, not verified: if Argon (52.6), Muse Spark 1.3 (48.1) and Muse Wonder (52.4) lost the same 27% as Sonnet, they would score 38.3, 35.0 and 38.2. All three would fall below the 48 reasoning floor, and the reasoning tier would be empty again.

*Blocks:* ratifying D1, since the bands were tuned on scores that mix efforts. It also makes reasoning-tier shadow results hard to interpret.

- (a) **Keep as is**, treating the plain score as the truth. Trade-off: no work, but the reasoning tier may be built on inflated scores.
- (b) **Max effort for everyone.** Score all models on the plain (max) score, as refresh 1 did, and call reasoning-tier models at high effort. Trade-off: like-for-like, but more reasoning tokens per call, so a reasoning request costs more than blended price suggests. It partly reverts D3, and the bands need retuning again (refresh 1 had Sonnet at 56.0 and Opus 5.5 at 57.6).
- (c) **Discount single-score models.** Compute the median max-to-medium gap over every AA family that publishes both a plain and a medium score. Apply it to plain-only models that accept a reasoning parameter, and mark them "effort-unverified" in the diff. Trade-off: keeps D3's principle that the score matches what we pay for, and it is cheap and reversible, but the score becomes an estimate, not a measurement.
- (d) **Let grading decide.** Keep the scores, and demote models that grade badly on the per-tier eval (the design's mitigation for "benchmark index ≠ our workload"). Trade-off: uses our own evidence, but it needs grades that do not exist yet, so it is slow.

*Build recommends:* (c) now, then (d) once grades exist. The gap would be measured across all paired families, not taken from Sonnet alone. Then retune D1 on the corrected scores.
*If no answer:* the build computes the gap statistics from the stored catalog and reports them in the next close-out. The live tiers are not changed.

### Q3. Which project is the Phase 1 shadow target?

This was an open question in the design. The build left it to the caller (`project_id`).

*Blocks:* plan step 5 and exit criteria 3 and 4 (cost reduction, quality loss).

- (a) Video Intelligence Pipeline agents, as the design suggested. Trade-off: the build has no information on its traffic mix or volume.
- (b) Another project (please name it). Trade-off: depends on that project.

*Build recommends:* (a), run in shadow mode against the Sonnet 5.5 baseline at $2.00 / $10.00 per 1M tokens, with 20% of requests also executed on the routed tier and stored for grading.
*If no answer:* the build waits. It does not start the shadow run on a project the design thread has not confirmed.

### Q4. Must each tier keep at least one member outside OpenRouter?

8 of the 9 ranked cloud members are called through OpenRouter. All three balanced members are `openrouter/...`. In fast, the three ranked members are `openrouter/...` and only local `ollama/qwen3:8b` is not. In reasoning, only `gemini/gemini-4-argon` is direct. If OpenRouter goes down, balanced has no members and its requests fall through to reasoning. That strains the exit criterion "zero unhandled failures when Jev or one provider is down", depending on whether OpenRouter counts as "one provider".

*Blocks:* the meaning of exit criterion 5.

- (a) **Rule:** each tier must include at least one member reachable without OpenRouter. If none ranks in the top 3, add the best qualifying direct-key model as an extra fallback. Trade-off: resilient, but sometimes adds a pricier fallback.
- (b) **Accept the concentration**, and count OpenRouter as one provider for the exit criterion. Trade-off: simple, but one outage empties a tier.

*Build recommends:* (a). Note that Q1 (a) already satisfies it for balanced.
*If no answer:* no change. The build tests an OpenRouter outage in the shadow run and reports what happened.

## Deviations from the design

All eight are implemented, reversible, and tagged with their IDs in config or code comments. Please ratify or reject each one.

| ID | Design said | Build did | Why (evidence) | Reversible? |
| --- | --- | --- | --- | --- |
| D1 | Quality floors fast ≥ 25, balanced ≥ 40, reasoning ≥ 55 ("starting proposals to tune after the first refresh") | fast ≥ 25 / ≤ $0.50, balanced ≥ 38 / ≤ $4.00, reasoning ≥ 48 / no ceiling | On medium-effort scores, the callable models had a median of 21.9, a p90 of 41.1 and a max of 52.6, so the 55 floor left reasoning empty. **Depends on Q2:** these bands were tuned on scores that mix efforts. | Yes (config) |
| D2 | "The monthly refresh fills it with every model that qualifies", with fallback one tier up | `exclusive_tiers: true`: each model joins only the highest tier it qualifies for | In refresh 1 (overlapping tiers), fast and balanced shared 2 of their 3 members (Ling 3.1 Flash, Claude Haiku 5.5). A fast-to-balanced fallback then retried the same models, and moving up a tier bought no extra capability. | Yes (flag) |
| D3 | Quality floor = "AA Intelligence Index", with no effort specified | Score each model at the first available effort in `[medium, high, plain, non-reasoning, low, …]`, and call it with `reasoning_effort` set to that effort (low/medium/high only, with `drop_params`) | AA's plain slug is maximum effort: Sonnet 5.5 scores 56.0 at max and 40.8 at medium. Scoring at max overstates what a default call delivers. **Gap:** this only affects models that publish effort variants (see Q2). | Yes (config list) |
| D4 | "Join on model name with a hand-kept alias map"; unmatched models are flagged, not tiered | Added lenient matching: strip date suffixes (`-0731`), trailing `-0`, try `-preview`. Distinct models rank ahead of dated snapshots of the same model, so a fallback is a different model. | Unmatched models dropped from 185 to 178 (of 344). The alias map is still empty. Dated snapshots of a model share its one AA score, so without the de-duplication they could fill the fallback slots with copies of the primary. | Yes |
| D5 | Phase 1 is "a Python library … No proxy, no UI" | Added `tokenrouter serve`: a local web app (standard library only, binds to 127.0.0.1) with a playground that traces each pipeline stage, tier/catalog charts and a request-log view | Built to inspect routing decisions and fallback behaviour. No new dependency. Endpoints that run models require an `X-Tokenrouter: 1` header and a localhost Host header. Simulated runs are logged in a separate database so they don't skew metrics. | Yes (separate module) |
| D6 | Open question: "auto-apply the new tiers, or wait for approval of the diff?" | Approval by default: `tokenrouter refresh` writes a candidate and a diff, and `tokenrouter apply` promotes it. `catalog.auto_apply: false` can be flipped. The first refresh applies itself, since there is nothing to compare against. | Tier changes then reach `tiers.yaml` only after review, and they show up in git history (`config/tiers.yaml` is tracked). | Yes (flag) |
| D7 | Open question: "add a fourth 'coding' tier keyed on the Coding Index?" | Not added. `coding_idx` and `agentic_idx` are stored on every catalog row. | Wait for refresh history and grades first. | Yes |
| D8 | "Keys from env only" | Keys come from the process environment, or are loaded from the project's `.env` file (gitignored, and never overriding variables already set). The scheduled job sources `~/.config/tokenrouter/env` (chmod 600), because launchd has no shell environment. | The scheduled refresh needs keys without an interactive shell. No key ever goes in tracked config, and `.env.*` variants are gitignored. | Yes |

**Values the build chose where the design gave no number** (FYI, no reply needed unless one looks wrong):

```yaml
decision:
  jev_model: jev-1.13.0
  jev_timeout_s: 2.0
  default_tier: balanced                 # Jev unavailable
  confidence_thresholds: {fast: 0.6, balanced: 0.5, reasoning: 0.3}   # below -> one tier up; reasoning -> human_review
  flag_threshold: 0.5
  sensitive_threshold: 0.8               # is_sensitive >= 0.8 -> human_review
  long_context_tokens: 200000
  state_prompt_chars: 4000               # truncated excerpt sent to Jev
  blocked_data_classes: [secrets, credentials]
tiers.requires: fast {min_context: 32000}; balanced/reasoning {min_context: 128000, tools: true}
execution: {num_retries: 1, timeout_s: 120, max_output_tokens_default: 4096}
```

An empty answer cut off at `max_tokens` with no tool call is treated as a failure. The router tries the next model in the chain and still bills the wasted call. This extends "fall back on error": a reasoning model can spend its whole token budget thinking and return nothing.

## Findings from real data

1. **First real refresh (2026-10-16):** 344 OpenRouter models and 703 AA models; 178 OpenRouter models had no AA match (185 before D4) and are listed in the diff, not tiered. That leaves over half the OpenRouter catalog untiered. The alias map has 0 entries.
2. **AA free endpoint:** `/api/v2/language/models` returns 403 on the free tier (it needs Pro). `/api/v2/data/llms/models` works on the free tier and has the same fields, so the build uses it.
3. **Effort variants:** AA lists one slug per reasoning effort, and the plain slug is maximum effort. Sonnet 5.5 scores 56.0 at max and 40.8 at medium.
4. **Refresh 1 tiers (plain scores, overlapping tiers, design bands 25/40/55):**
   - reasoning: Sonnet 5.5 (56.0, $4.00), Opus 5.5 (57.6, $8.00)
   - fast and balanced both included Ling 3.1 Flash (41.1, $0.1125) and Haiku 5.5 (43.4, $0.20)
5. **Medium-effort distribution:** callable models had a median score of 21.9, a p90 of 41.1 and a max of 52.6.
6. **Refresh 2 tiers (live now; medium-effort scoring, exclusive tiers, bands 25/38/48).** Quality per dollar = score / blended price:

   | Tier | # | Model (called as) | AA score | Blended $/1M | Quality per $ |
   | --- | --- | --- | --- | --- | --- |
   | fast | 1 | openrouter/deepseek/deepseek-v4-flash | 34.3 | 0.11375 | 301.5 |
   | fast | 2 | openrouter/xiaomi/mimo-v2.6-flash | 37.9 | 0.175 | 216.6 |
   | fast | 3 | openrouter/upstage/solar-pro4 | 28.2 | 0.1575 | 179.0 |
   | fast | 4 | ollama/qwen3:8b (local, fixed extra member) | — | 0 | — |
   | balanced | 1 | openrouter/inclusionai/ling-3.1-flash | 41.1 | 0.1125 | 365.3 |
   | balanced | 2 | openrouter/deepseek/deepseek-v4.1-flash | 39.5 | 0.178763 | 221.0 |
   | balanced | 3 | openrouter/z-ai/glm-5.3-flash | 41.8 | 0.2375 | 176.0 |
   | reasoning | 1 | openrouter/meta/muse-spark-1.3 | 48.1 | 2.00 | 24.05 |
   | reasoning | 2 | gemini/gemini-4-argon | 52.6 | 4.00 | 13.15 |
   | reasoning | 3 | openrouter/meta/muse-wonder | 52.4 | 8.00 | 6.55 |

7. **All 9 ranked members have a plain AA slug** (no effort suffix), so none is called with `reasoning_effort`. Medium-effort scoring therefore lowered only the models that publish effort variants, which includes Anthropic's (see Q2).
8. **No Anthropic model is in any top 3.** Sonnet 5.5 at medium (40.8, $4.00) qualifies for balanced, but its quality per dollar is 10.2 against 176.0 for balanced's #3.
9. **Balanced's $4.00 ceiling has no effect in practice:** all three balanced members cost $0.24 or less.
10. **Provider concentration:** 8 of 9 ranked cloud members are called through OpenRouter (see Q4).
11. **Live latency and cost:** Jev `jev-1.13.0` decisions took about 220-250 ms over a handful of live calls; that is not a p95. One routed fast-tier call cost $0.000013.
12. **Tests:** 61 tests pass offline (HTTP mocked, LiteLLM `mock_response`). They cover Jev unavailable → balanced, fallback within a tier and then one tier up, all models down → logged `RoutingError`, a Jev plus provider outage together, and the shadow-mode baseline never breaking when the decision step fails.

## Plan vs built

| Plan step | Status | Notes |
| --- | --- | --- |
| 1. Catalog refresher: fetch, join with alias map, bands, diff, monthly task, `tiers.yaml` | changed (D1, D2, D3, D4, D6) | Two real refreshes run by hand. launchd job (1st of each month, 06:00) and script written; no unattended run observed yet. |
| 2. Execution wrapper: LiteLLM SDK pinned and hash-verified, one group per tier, fallbacks fast → balanced → reasoning, keys from env, Ollama qwen3 in fast | done (D8) | LiteLLM pinned to 1.103.0 and installed with `--require-hashes`. `tokenrouter audit` checks pinned versions and unexpected `.pth` files. |
| 3. Jev client: Choice tier + 4 Noul flags; hard rules in code first; low confidence → up one tier; Jev down → balanced | done | Thresholds listed above. Projects with `send_raw_prompt: false` send Jev only a caller summary and metadata. |
| 4. Decision + cost log | done | SQLite. Per request: prompt hash, Jev version, probabilities, tier, model, tokens, USD, latency, fallback used. |
| 5. Shadow run + eval (100+ graded samples) | partial | Tooling built: shadow mode, `eval export/import`, and `report`, which checks the exit criteria. Run not started; blocked on Q3. No grades yet. |
| (not in plan) Local web app | added (D5) | |

## Exit criteria

| Criterion | Status | Measured value |
| --- | --- | --- |
| Monthly refresh runs unattended and produces a reviewable diff | partial | The diff is produced (two manual refreshes). Unattended run not yet observed. |
| Router adds < 600 ms p95 overhead | not measured | About 220-250 ms per Jev decision over a few live calls; no p95 yet. |
| ≥ 40% cost reduction vs all-Sonnet baseline | not measured | Shadow run not started. |
| Quality loss ≤ 5% on the graded sample | not measured | 0 graded pairs. |
| Zero unhandled failures when Jev or one provider is down | partial | Passes in offline tests. Not tested live. See Q4 for the OpenRouter case. |

## Next steps

Once the decisions are answered:

1. Q2: compute the max-to-medium gap across all paired AA families and apply the chosen rule. Re-run the refresh, retune the bands if needed, and report the new tiers and the band evidence (this closes out D1).
2. Q1 and Q4: add the pinned or direct-provider members as decided, then refresh and diff.
3. Add aliases for notable unmatched models, starting with any well-known model among the 178 unmatched.
4. Q3: start the shadow run on the confirmed project. Collect 100+ graded pairs, then report p95 overhead, cost reduction against the Sonnet baseline, and quality loss.
5. Observe the first unattended monthly refresh (1 November, 06:00).

## Reply (design thread fills in)

```markdown
## Reply (design thread fills in)
- Q1 (keep Sonnet/Anthropic in a tier): (a) pinned member / (b) provider weight / (c) score at called effort / (d) leave out / other: …
- Q2 (single-score models vs medium-effort scores): (a) keep / (b) max effort for all / (c) discount single-score / (d) grading decides / other: …
- Q3 (shadow target): (a) Video Intelligence Pipeline / (b) other: …
- Q4 (non-OpenRouter member per tier): (a) require / (b) accept / other: …
- D1 bands 25/38/48: ratify / reject: …
- D2 exclusive tiers: ratify / reject: …
- D3 medium-effort scoring + reasoning_effort on calls: ratify / reject: …
- D4 lenient name matching + snapshot de-dup: ratify / reject: …
- D5 local web app in Phase 1: ratify / reject: …
- D6 approval before applying refreshes: ratify / reject: …
- D7 no coding tier yet: ratify / reject: …
- D8 keys from env or gitignored .env file: ratify / reject: …
- Anything else the build should know:
```
