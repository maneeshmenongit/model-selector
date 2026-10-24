# tokenrouter — guide for Claude Code sessions

Token-aware model router, Phase 1 of `docs_to_claude/base_architecture/phase 1/tokenrouter-design.md`, as amended by
`docs_to_claude/base_architecture/phase 2/tokenrouter-phase1b-next-steps.md` (which wins on conflict).
Jev (TypeSafe) picks the tier, LiteLLM picks the model inside it; a monthly refresh re-ranks tiers
from OpenRouter prices and Artificial Analysis (AA) scores. README.md documents usage.

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

Enforced by checks N-T1…N-T6 (`tests/test_neutrality.py`, `tokenrouter audit`). Every close-out
gets a **Neutrality check** section: N-T1…N-T6 pass/fail, provider share per tier, and each
provider-naming recommendation with the user quote that justifies it (or "none").

## How this project is run

- **Two threads.** Design happens in a separate claude.ai *design thread*; this repo is the *build*.
  Messages travel through `docs_to_claude/` (in) and `docs_from_claude/` (out, plus the Q/D ledger
  `INDEX.md`). Use the `syncing-with-design-thread` skill to onboard, log deviations and close out.
- **Session start:** onboard with that skill, then read `HANDOFF.md`, which holds where the last
  session stopped and what to do next.
- **Don't build past an open blocking ledger item.** Any change to a number, rule, component or
  vendor named in `docs_to_claude/` gets a ledger entry, even when the user asks for it.
- **Session end** (or "prepare the next thread"): rewrite `HANDOFF.md` per the skill; lasting lessons go here.
- The user wants work committed **and pushed** to GitHub (public repo
  github.com/maneeshmenongit/model-selector) when a piece is done.

## Commands

```sh
uv sync                    # Python 3.13 venv; supply-chain install: see README "Setup"
uv run pytest              # ~100 tests, offline (httpx.MockTransport, LiteLLM mock_response)
uv run tokenrouter refresh # live feeds -> data/tiers.candidate.yaml + data/diffs/*.md (needs keys)
uv run tokenrouter apply   # promote the candidate to config/tiers.yaml (tracked in git)
uv run tokenrouter tiers | decide "…" | ask "…" | report | eval export/import | audit
uv run tokenrouter serve   # web app on http://127.0.0.1:8787 ; --demo for fictional data
```

Port 8765 is taken by an unrelated process on this Mac; the app defaults to 8787.

## Environment facts

- **Keys** live in `.env` (gitignored, chmod 600): TYPESAFE, ARTIFICIAL_ANALYSIS, OPENROUTER, ANTHROPIC,
  GEMINI, OPENAI, GROQ (Groq unused: no route). The CLI loads `.env` itself; library callers use
  `tokenrouter.config.load_dotenv`. **Never print key values**; check names/lengths only.
- **LiteLLM is pinned to 1.103.0, hash-locked** (`requirements.lock`) because of the Mar 2026 PyPI
  compromise. Don't bump it without a release ≥ 2 weeks old and regenerated hashes; `tokenrouter audit` checks.
- **AA:** use the free endpoint `/api/v2/data/llms/models` (`/api/v2/language/models` is Pro-only, 403).
  Free quota is 100 requests/day. The plain slug is the model's **max-effort** score; effort variants
  are `-medium`, `-high`, etc.
- **Jev** is pinned to `jev-1.13.0` (`jev-1.14.0` is preview). About 220–250 ms per live decision.
- **Ollama** runs locally with `qwen3:8b` (fast tier's last member).

## Gotchas learned the hard way

- LiteLLM `model_info.tier` is reserved ("free"/"paid"); we use `router_tier`. Don't put
  `input_cost_per_token` in `model_info` (LiteLLM registers it globally); we use `usd_per_input_token`.
- In-tier fallback relies on LiteLLM's `order` in `litellm_params`; the answering deployment is
  `response._hidden_params["model_id"]` (`<tier>-<rank>`).
- Reasoning models can return an empty answer when `max_tokens` is small; the router treats an
  empty, truncated answer as a failure and tries the next model.
- `config/tiers.yaml` is generated: change `config/tokenrouter.yaml` and refresh instead of hand-editing.

## Before every commit

- Scan the staged diff for real key values from `.env` (compare values, never print them); the repo is public.
- Author must be `Maneesh Menon <chat2maneesh@gmail.com>`; re-check after cherry-picks (machine
  hostname emails have leaked in before). End messages with the Co-Authored-By line from the session.
