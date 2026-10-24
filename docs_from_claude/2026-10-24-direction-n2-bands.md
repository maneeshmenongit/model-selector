---
type: direction
blocking: yes (applying refresh 4 to the live tiers; N3 and N4 continue)
date: 2026-10-24
answers: [base_architecture/phase 2/tokenrouter-phase1b-next-steps.md]
---

# Direction: N2 is built; the p50/p75/p90 floors leave a weak reasoning tier (Q6)

Replies to "tokenrouter — Phase 1b" (2026-10-16), N2. Effort normalization and percentile bands
are built exactly as specified, and they ran on live data (refresh 4, 2026-10-24). All six neutrality
checks still pass, now running through the full normalize → bands → rank pipeline. The build has
**not** applied refresh 4: the live tiers are still refresh 2's. This file reports the result and
asks one question.

## 1. What the design says

> "Re-derive bands from the normalized distribution of callable models: fast floor = p50, balanced
> floor = p75, reasoning floor = p90 (configurable). Keep price ceilings; report which ceilings bind."
> (phase1b, N2)

## 2. What the build found (refresh 4, live feeds, 2026-10-24)

**Effort gap (Q2).** 27 AA families publish both a plain (max-effort) and a medium score.
Median gap 11.1%, p25 3.4%, p75 18.2%. Gaps range from -3.1% (medium scores above plain) to 27.1%.

**Score basis of the 168 callable models:** 29 `measured` (AA scored them at the effort we call at),
106 `effort_unverified` (plain score × 0.889, now called with `reasoning_effort: medium`), 33
`no_effort_param` (plain score kept: no reasoning parameter on OpenRouter). The same rule applies to
every provider.

**Derived bands** (percentiles of 162 callable models' normalized scores, one per score entry):

| Tier | Percentile | Floor (was fixed) | Price ceiling | Clear the floor but over the ceiling |
| --- | --- | --- | --- | --- |
| fast | p50 | 20.2 (25) | $0.50 | 64 (binds) |
| balanced | p75 | 30.1 (38) | $4.00 | 12 (binds) |
| reasoning | p90 | 39.8 (48) | none | 0 |

**New tiers** (score = normalized; q/$ = score per $ blended):

| Tier | #1 | #2 | #3 | Top-3 mean score |
| --- | --- | --- | --- | --- |
| fast | solar-mini4 21.4, $0.087, q/$ 244.8 | ling-3.0-flash-vl 21.9, $0.090, 243.0 | gpt-6-luna 29.9, $0.200, 149.5 | 24.4 |
| balanced | deepseek-v4-flash-0731 30.5, $0.067, 457.4 | ling-3.1-flash 36.5, $0.113, 324.7 | mimo-v2.6-flash 33.7, $0.175, 192.5 | 33.6 |
| reasoning | deepseek-v4.1-flash-1017 40.1, $0.263, 152.7 | glm-5.3 39.8, $0.409, 97.3 | mimo-v2.6-pro 41.2, $0.544, 75.7 | 40.4 |

Plus local qwen3:8b as fast #4. 8 of 9 cloud members are called through OpenRouter (N3 addresses this).

**The problem.** Because the floor is the 90th percentile of *all* callable models, including a long
tail of older, weaker ones, the reasoning floor (39.8) is close to balanced's best member (36.5).
Ranking by quality per dollar then fills reasoning with the cheapest models that just clear 40.
Escalating balanced → reasoning buys +6.8 points on average. In the live tiers (refresh 2), reasoning's
top 3 published scores averaged 51.0; normalized, they would average 45.4. The eight highest normalized
scores among callable models (46.3-51.2) rank 6th-17th in reasoning, outside the top 3.

**Alternatives, computed on the same refresh** (offered as evidence only; nothing is tuned to a model):

| Percentiles | fast floor / top-3 mean | balanced floor / top-3 mean | reasoning floor / top-3 mean / avg $ |
| --- | --- | --- | --- |
| p50 / p75 / p90 (as specified) | 20.2 / 24.4 | 30.1 / 33.6 | 39.8 / 40.4 / $0.41 |
| p60 / p85 / p95 | 22.8 / 32.9 | 36.5 / 37.9 | 44.8 / 47.7 / $3.67 |
| p60 / p85 / p97 | 22.8 / 32.9 | 36.5 / 37.9 | 46.9 / 49.3 / $5.33 |

## 3. Q6. Which percentiles should set the floors?

*Blocks:* applying refresh 4 (or any later refresh) to the live tiers. N3 and N4 don't wait on it.

- (a) **Keep p50/p75/p90.** Exactly as specified, and the cheapest by far (reasoning averages $0.41/M).
  But reasoning is barely above balanced, so escalation does little for hard work.
- (b) **Raise them to p60/p85/p95.** Still derived from the distribution, the same for every provider.
  Each step up buys clearly more (32.9 → 37.9 → 47.7), but reasoning costs about 9× more ($3.67/M).
- (c) **p60/p85/p97.** The strongest reasoning tier (49.3), at $5.33/M.
- (d) **Different population or ranking:** e.g. percentiles over only the models that clear a capability
  bar, or rank reasoning by score instead of score per dollar. Bigger design change; the build would
  need it specified.

*Build recommends:* (b). The reasoning tier exists for hard work, and under (a) it buys too little. (b)
gets there by moving the percentiles alone, without touching the ranking rule. The extra cost only applies
to requests Jev sends to reasoning.
*If no answer:* the live tiers stay on refresh 2, the config stays at p50/p75/p90 as specified, and
nothing is applied until the design thread replies.

## 4. Deviations to ratify

| ID | Design said | Build did | Why (evidence) | Reversible? |
| --- | --- | --- | --- | --- |
| D10 | Normalize plain-only scores (N2); nothing on how those models are called | Rows flagged `effort_unverified` are called with `reasoning_effort: medium` | Their score is now a medium-effort estimate. Calling them at max effort would pay for more than the score credits (D3 logic) | Yes, one line in tiers_file |
| D11 | "plain-only models" | Normalizes every row *scored from* a plain slug, including families that list other variants but no medium or high | The score in use is a max-effort score either way; 139 of 168 callable rows are scored from a plain slug | Yes |

## Reply (design thread fills in)

- Q6: (a) / (b) / (c) / (d) / other: …
- D10: ratify / reject: …
- D11: ratify / reject: …
- Anything else the build should know:
