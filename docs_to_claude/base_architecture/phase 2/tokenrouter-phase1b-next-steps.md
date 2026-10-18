---
type: design-reply + next-phase plan
date: 2026-10-16
answers: 2026-10-16-closeout-phase1-build.md
supersedes_in: base_architecture/tokenrouter-design.md (sections noted under "Design corrections")
---

# tokenrouter — Phase 1b: decisions, provider neutrality, next steps, Phase 2

This document answers the Phase 1 build close-out, adds a non-negotiable provider-neutrality rule, and lays out the work to finish Phase 1 and the plan for Phase 2.

**Read section 1 first. It overrides anything else in this repo that conflicts with it.**

---

## 1. Provider neutrality (non-negotiable, P0)

### Why this exists

The point of tokenrouter is to pick models on evidence, not on brand. The Phase 1 close-out recommended pinning an Anthropic model into a tier even though its quality per dollar was 17x worse than the model beside it, and it attributed that wish to the user. The user never asked for this. The original design doc also anchored on Anthropic: an all-Sonnet cost baseline, "Sonnet-class" tier examples, and provider-based tie-breaks. All of that is withdrawn.

### The rule (copy verbatim into `CLAUDE.md`)

```markdown
## Provider neutrality — non-negotiable
- Ranking uses only: normalized quality score, blended price, and capability requirements
  (context window, tools, JSON). Nothing else.
- No provider or model name may appear in ranking logic, band logic, or tier config.
  The only exception is `extra_members` for LOCAL models (ollama/*).
- No Anthropic, OpenAI, Google or any other vendor gets a pinned slot, a weight,
  a tie-break advantage, or a softer score correction than any other vendor.
- Any recommendation in a close-out that names a specific provider or model to add, keep,
  or favour must quote the user's own words asking for it. If there is no quote, do not make it.
- Never write "the user wants X" unless the user wrote it. Cite the message.
- Corrections (effort normalization, aliasing, de-dup) apply identically to every provider.
- Bands are derived from the score distribution, never tuned to include or exclude a named model.
- If the evidence says a vendor's model loses, it loses. Report it plainly.
```

### Enforcement (build these before any other work)

| ID | Check | How | Fails when |
| --- | --- | --- | --- |
| N-T1 | Label-shuffle test | Take the real catalog snapshot. Randomly permute `provider` and `model_id` strings across rows, keeping score/price/capabilities attached to the row. Re-rank. | Tier membership (by row identity) changes |
| N-T2 | Rename test | Replace every provider string with a random token. Re-rank. | Any tier or rank changes |
| N-T3 | Static check | Test that scans the ranking and band modules for provider/vendor string literals (`anthropic`, `claude`, `openai`, `gpt`, `gemini`, `google`, `meta`, `deepseek`, …). | Any literal found |
| N-T4 | Config lint | `tokenrouter audit` rejects `extra_members` entries that are not `ollama/*` (or another configured local prefix). | A cloud model is pinned |
| N-T5 | Tie-break determinism | Ties resolve by: lower blended price → higher normalized score → larger context window → stable hash of the catalog row id. No "provider we hold a key for". | Tie-break reads provider |
| N-T6 | Provider-share report | Every refresh diff prints, per tier, which providers/gateways members come from and why each made it (score, price, q/$). | Missing from diff |

All six run in CI (`pytest`) and in `tokenrouter audit`.

### Close-out requirement

Every future close-out gets a **Neutrality check** section with: N-T1…N-T6 pass/fail, provider share per tier, and a list of any recommendation that names a provider, each with the user quote that justifies it (or "none").

---

## 2. Reply to the Phase 1 close-out

```markdown
## Reply (design thread fills in)
- Q1: (d) leave out. The user did not ask for Sonnet or any Anthropic model to be kept in a tier.
  Do not attribute preferences that were not stated. Remove the pinned-member proposal.
  `extra_members` is for local models only.
- Q2: (c) then (d). Gap = median max→medium gap across ALL AA families that publish both,
  applied identically to every plain-only model that accepts a reasoning parameter, any provider.
  Mark those rows `effort_unverified`. Accept whatever tiers result. Then retune bands (D1).
- Q3: (a) Video Intelligence Pipeline. Baseline = the model each agent uses today,
  NOT all-Sonnet. Record the baseline per agent in config.
- Q4: (a) require at least one member per tier reachable without OpenRouter, chosen by the same
  score/price ranking among direct-key or local models — any provider.
- D1: ratify provisionally; re-derive after Q2 using the percentile rule in N2.
- D2: ratify.
- D3: ratify.
- D4: ratify.
- D5: ratify, scope frozen — no further UI work until the shadow run reports.
- D6: ratify.
- D7: ratify.
- D8: ratify.
- Build-chosen values (thresholds, timeouts): accepted for now; revisit with shadow-run data.
- Anything else: Section 1 (provider neutrality) is P0 and blocks all other work.
```

---

## 3. Design corrections (apply to `base_architecture/tokenrouter-design.md`)

| Old text | New text |
| --- | --- |
| "≥ 40% cost reduction vs all-Sonnet baseline" | "≥ 40% cost reduction vs each agent's current model (recorded baseline)" |
| Tier examples "Haiku-class", "Sonnet-class", "Opus / Fable-class" | Remove vendor examples. Describe tiers by work type and band only. |
| "Ties go to the provider you already hold a key for." | Tie-break order in N-T5. |
| Example class column naming vendors | Delete the column. |

---

## 4. Next steps to finish Phase 1 (in order)

Each item lists what to build and how we know it's done. Explain the approach in the close-out before showing code.

### N1. Provider-neutrality guard (P0)
- Build N-T1…N-T6 and the `CLAUDE.md` rule from Section 1.
- Remove any Anthropic-specific config, comments or proposals added in Phase 1.
- **Done when:** all six checks pass in CI, and the current tiers are unchanged by N-T1/N-T2.

### N2. Effort normalization (Q2) and band re-derivation (D1)
- From the stored catalog, find every AA family with both a plain (max) slug and a medium slug. Report count, median gap, p25/p75 gap, and the list.
- For plain-only models that accept `reasoning_effort`: normalized score = plain score × (1 − median gap%). Flag `effort_unverified`. Models that don't accept a reasoning parameter keep their plain score, flagged `no_effort_param`.
- Score every provider the same way. No per-vendor gap.
- Re-derive bands from the normalized distribution of callable models: fast floor = p50, balanced floor = p75, reasoning floor = p90 (configurable). Keep price ceilings; report which ceilings bind.
- **Done when:** the refresh diff shows the gap statistics, the derived bands, and the new tiers, with N-T1/N-T2 still passing.

### N3. Direct-provider fallback per tier (Q4)
- After ranking, if a tier has no member reachable without OpenRouter, add the best-ranked qualifying direct-key or local model as an extra fallback (same ranking rule, any provider).
- **Done when:** every tier has ≥ 1 non-OpenRouter member, and an offline test with OpenRouter down routes every tier without falling through.

### N4. Alias map for unmatched models
- 178 of 344 OpenRouter models are unmatched. Rank them by OpenRouter usage or release recency, alias the top 25, and list the rest in the diff.
- No preference for any vendor in which models get aliased first.
- **Done when:** the unmatched count is reported before/after, with the alias list in git.

### N5. Shadow run on Video Intelligence Pipeline (Q3)
- Record the current model per agent as the baseline.
- Shadow mode: the baseline answers; 20% of requests also run on the routed tier and are stored for grading.
- Grade ≥ 100 pairs: "Would the routed answer have been acceptable?" Grading is blind to which model produced each answer.
- **Done when:** `tokenrouter report` shows cost reduction vs baseline, quality loss, and p95 router overhead.

### N6. Unattended refresh
- Observe the 1 November 06:00 run. Confirm the candidate + diff were written and nothing was auto-applied.
- **Done when:** the diff from that run is attached to the next close-out.

### N7. Live failure tests
- Simulate Jev down, OpenRouter down, and one direct provider down against live config (no real spend beyond test calls).
- **Done when:** zero unhandled failures, logged per scenario.

### Phase 1 exit criteria (updated)

- [ ] Provider-neutrality checks N-T1…N-T6 pass
- [ ] Monthly refresh runs unattended and produces a reviewable diff
- [ ] Router adds < 600 ms p95 overhead
- [ ] ≥ 40% cost reduction vs each agent's current model
- [ ] Quality loss ≤ 5% on ≥ 100 blind-graded pairs
- [ ] Zero unhandled failures when Jev, OpenRouter, or one direct provider is down

---

## 5. Phase 2 plan (starts only after Phase 1 exit)

| ID | Work | Why | Gate |
| --- | --- | --- | --- |
| P2-1 | Grade-driven demotion | Benchmarks ≠ our workload. Models that grade below the tier's acceptance bar on our eval set are demoted at the next refresh, any provider. | Needs N5 grades |
| P2-2 | Per-tier eval on every refresh | Run the graded set against new tier members before `apply`. Diff shows score, price and our own pass rate. | P2-1 |
| P2-3 | LiteLLM proxy | Lets other tools (including coding agents) use tokenrouter through an OpenAI- or Anthropic-compatible endpoint. Same neutrality rules apply to proxy routes. | Phase 1 exit |
| P2-4 | Jev as LiteLLM auto-router classifier plugin | Replaces our wrapper's decision step with LiteLLM's plugin hook, so the proxy and the library share one decision path. | P2-3 |
| P2-5 | Per-project budgets | Monthly USD cap per `project_id`; over budget → drop one tier or queue. | P2-3 |
| P2-6 | Coding tier decision (D7) | Decide with grades whether a Coding-Index tier beats the general tiers for code tasks. | P2-1 |
| P2-7 | Neutrality audit report | Monthly: provider share per tier, plus spend share per provider vs quality-per-dollar share. Flags drift toward any single vendor. | Phase 1 exit |

**Phase 2 exit criteria (draft):**
- [ ] Every tier member has our own pass rate, not only an AA score
- [ ] Proxy serves at least one external tool with the same routing as the library
- [ ] Per-project budgets enforced with zero overspend in a month
- [ ] Neutrality audit runs monthly and shows no unexplained vendor concentration

---

## 6. What the next close-out must contain

1. **Neutrality check** (Section 1): N-T1…N-T6 results, provider share per tier, and provider-naming recommendations with user quotes.
2. N2 gap statistics, derived bands, and the new tiers with quality-per-dollar figures.
3. Status of N3–N7 against their "done when" lines.
4. Updated Phase 1 exit-criteria table with measured values.
5. Any new deviation, in the D-table format, with evidence.
