# tokenrouter

Token-aware model router, Phase 1 of [the design](<docs_to_claude/base_architecture/phase 1/tokenrouter-design.md>), as amended by [Phase 1b](<docs_to_claude/base_architecture/phase 2/tokenrouter-phase1b-next-steps.md>).
Jev picks the **tier**; LiteLLM picks the **model inside it**.

| Layer | Runs | Module |
| --- | --- | --- |
| Catalog refresh | Monthly | `tokenrouter.catalog` — OpenRouter + Artificial Analysis → bands → rank → diff → `tiers.yaml` |
| Decision | Every request | `tokenrouter.decision` — hard rules in code → one Jev call → escalation → context floor |
| Execution | Every request | `tokenrouter.execution` — LiteLLM Router, ordered members per tier, fallback one tier up |
| Log | Every request | `tokenrouter.log` — SQLite: decision, tier, model, tokens, USD, latency, Jev version |

## Setup

```sh
uv venv -p 3.13
uv pip install --require-hashes -r requirements.lock   # hash-verified, LiteLLM pinned to 1.103.0
uv pip install --no-deps -e .
tokenrouter audit                                      # pinned versions, .pth files, provider neutrality
```

(`uv sync` works for development too; the lockfile is regenerated with
`uv export --format requirements-txt --no-dev --no-emit-project > requirements.lock`.)

Keys come from the environment or a gitignored `.env` at the project root (shell variables win) — see [.env.example](.env.example):

| Variable | Used for |
| --- | --- |
| `ARTIFICIAL_ANALYSIS_API_KEY` | Quality indices for the monthly refresh (required for `refresh`) |
| `TYPESAFE_API_KEY` | Jev decisions; without it every request takes the default tier (balanced) |
| `OPENROUTER_API_KEY` | Reach any catalog model through OpenRouter |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, … | Call that provider's models directly instead of through OpenRouter (routing only: ranking never looks at providers or keys) |

## Monthly refresh

```sh
tokenrouter refresh        # fetch feeds, re-rank, write data/diffs/<date>-refresh-<id>.md
tokenrouter apply          # promote data/tiers.candidate.yaml -> config/tiers.yaml after review
tokenrouter tiers          # show the live tiers
scripts/install_launchd.sh # schedule it: 1st of each month, 06:00 (keys from ~/.config/tokenrouter/env)
```

- Blended price = 0.75 × input + 0.25 × output (USD / 1M tokens).
- A model joins the **highest** tier whose price ceiling, quality floor and capability requirements
  it meets (`exclusive_tiers: true`), so moving a request up a tier always buys a more capable model.
  Inside a tier models are sorted by `intelligence_idx / blended_price`; ties go to the lower price,
  then the higher score, then the larger context window, then a stable hash of the model id. The top 3
  distinct models become primary + fallbacks (LiteLLM `order` 1–3).
- Artificial Analysis scores each reasoning-effort setting separately, and the plain slug is maximum
  effort. Models are scored at **medium** effort when that variant exists
  (`score_effort_preference`), and called at that effort (`reasoning_effort`), so the score matches
  what you pay for. A model with only a plain score that takes a reasoning parameter is lowered by the
  median max→medium gap across every family that publishes both, flagged `effort_unverified`, and
  called at medium; one that takes no reasoning parameter keeps its plain score (`no_effort_param`).
- Quality floors are derived each refresh: a percentile of callable models' normalized scores
  (`catalog.band_percentiles`, default p50 / p75 / p90). Price ceilings are fixed in config. The diff
  prints the gap statistics, the floors, and which ceilings bind.
- Names are matched leniently: date suffixes (`-0731`), `-0`, and `-preview` variants.
- The first refresh applies itself (nothing to review against). Later refreshes wait for
  `tokenrouter apply` unless `catalog.auto_apply: true`.
- Models whose names don't match across the feeds are listed in the diff, not tiered. Map them in
  [config/aliases.yaml](config/aliases.yaml).
- Local `ollama/qwen3:8b` is appended to the fast tier as its last member (`extra_members`).

Tier bands, thresholds and budgets live in [config/tokenrouter.yaml](config/tokenrouter.yaml).

## Using it from an agent

```python
from tokenrouter import TokenRouter, HumanReviewRequired, BudgetExceeded, RoutingError

tr = TokenRouter()                                   # live mode
res = tr.complete("Tag this review: 'great battery'", project_id="video-intel", max_tokens=200)
print(res.text, res.tier, res.model, res.usd)

tr.decide("…")                                       # dry run: decision only, nothing called or logged
```

Shadow mode keeps each agent's current model in charge and logs what the router would have done.
Record that model per agent under `projects.<project_id>.baseline` in `config/tokenrouter.yaml`
(or pass `baseline_model=`):

```python
tr = TokenRouter(mode="shadow", shadow_execute_rate=0.2)  # 20% also run on the routed tier, stored for grading
res = tr.complete(messages, project_id="<agent>")       # res.response is always that agent's baseline's
```

Request flow:

1. **Hard rules (code):** declared or detected blocked data classes (`secrets`, `credentials`) →
   human review; project monthly budget reached → `BudgetExceeded`.
2. **Jev:** one call — Choice `tier` (fast / balanced / reasoning / human_review) plus Noul flags
   `needs_tools`, `needs_long_context`, `needs_current_info`, `is_sensitive`. Jev gets a truncated
   excerpt plus metadata in words (no token counts); projects with `send_raw_prompt: false` send only
   the caller's `summary=`. Jev unavailable → `default_tier` (balanced).
3. **Escalation:** `is_sensitive` ≥ 0.8 → human review; confidence below the tier threshold → one
   tier up (reasoning → human review).
4. **Context floor (code):** escalate until the tier's primary fits prompt + max output tokens and
   required tools. Flags Jev inferred are soft: if nothing satisfies them, they are dropped.
5. **Execution:** LiteLLM tries the tier's members in rank order, then the next tier up. If all
   fail, `RoutingError` is raised and logged.

Human-review requests raise `HumanReviewRequired` and land in the `review_queue` table.

## Web app

```sh
tokenrouter serve --demo     # fictional models, works with no API keys
tokenrouter serve            # your real config/tiers.yaml and request log
```

Open http://127.0.0.1:8787 (change it with `--port`).

- **Playground:** type a prompt or pick a scenario and watch each stage: hard rules, Jev, escalation,
  context floor, execution. You can simulate Jev's answer (tier, confidence, flags) or a Jev
  outage, and take models down to watch the fallback chain. "Simulated models" runs LiteLLM for real
  with mock responses. "Real call" calls the model and costs money.
- **Tiers & catalog:** quality vs blended price with each tier's band drawn on it, the ranked
  members per tier, and the refresh diff.
- **Request log:** the exit criteria, the tier mix, and recent requests with their decision details.
  Simulated playground runs are kept in a separate log (`data/playground.db`) so they don't skew
  real metrics.

The server uses only the standard library and binds to 127.0.0.1. Requests that run a model need
an `X-Tokenrouter: 1` header and a localhost `Host` header, so a page open in your browser can't
make paid calls through it.

## Evaluating Phase 1

```sh
tokenrouter report --project video-intel --mode shadow    # cost, p95 overhead, fallbacks vs exit criteria
tokenrouter eval export -n 100 --out eval_sample.csv      # fill `grade` with 1/0 (y/n)
tokenrouter eval import eval_sample.csv
```

The `report` command checks the exit criteria directly: p95 decision overhead < 600 ms, ≥ 40 %
cheaper than each agent's current model (`projects.<agent>.baseline` in config; n/a until recorded), quality
loss ≤ 5 % over ≥ 100 graded pairs, and zero failures.

## Provider neutrality

Ranking reads only the quality score, blended price and capabilities; no provider gets a pinned
slot, a weight or a tie-break. Six checks prove it, in `pytest` and in `tokenrouter audit`:

| Check | Fails when |
| --- | --- |
| N-T1 label shuffle | Permuting provider/model names across catalog rows changes any tier |
| N-T2 rename | Replacing every provider string with a random token changes any tier or rank |
| N-T3 static | The ranking module or tier bands name a vendor, or ranking reads a provider/route field |
| N-T4 config lint | `extra_members` pins a model that is not local (`catalog.local_model_prefixes`) |
| N-T5 tie-break | Ties don't resolve by lower price → higher score → larger context → id hash |
| N-T6 provider share | The refresh diff lacks the per-tier provider/gateway table with each member's evidence |

N-T1/N-T2 run on the latest local refresh (`data/catalog.db`, not committed) and on a synthetic
catalog in the test suite.

## Tests

```sh
uv run pytest
```

Tests run offline: HTTP is mocked with `httpx.MockTransport` and model calls use LiteLLM
`mock_response`.

## Tier bands

The design's starting floors (25 / 40 / 55) left the reasoning tier empty once models were scored at
medium effort. After the first real refresh (2026-10-16), callable models had a median score of 21.9,
a p90 of 41.1 and a maximum of 52.6, and the bands were retuned to:

| Tier | Price ceiling (blended $/1M) | Quality floor |
| --- | --- | --- |
| fast | ≤ 0.50 | ≥ 25 |
| balanced | ≤ 4.00 | ≥ 38 |
| reasoning | none | ≥ 48 |

## Empty answers

A reasoning model can spend its whole `max_tokens` budget thinking and return nothing. An empty
answer cut off at the limit (no tool call) counts as a failure: the router tries the next model in
the chain and still bills the wasted call. If every model does this, `RoutingError` says to raise
`max_tokens`.

## Decisions taken on the design's open questions

These and every other change from the design are tracked (Q/D IDs) in
[docs_from_claude/INDEX.md](docs_from_claude/INDEX.md); the design thread ratified D1–D8 in Phase 1b.

- **Auto-apply vs approval:** approval by default (`tokenrouter apply`); flip `catalog.auto_apply`.
- **Coding tier:** not added; `coding_idx` and `agentic_idx` are stored in the catalog so a fourth
  tier can be added after the first refreshes.
- **Shadow target project:** left to the caller (`project_id`); the design suggests the Video
  Intelligence Pipeline agents.
