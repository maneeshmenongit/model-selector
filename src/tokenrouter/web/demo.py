"""Demo workspace: fictional models run through the real refresh pipeline.

Lets the web app show catalog, tiers, diffs and routing before any API key exists.
Every model name here is made up (providers acme, orbit, lumen, nimbus, quill) and
every price / score is illustrative, not a looked-up fact.
"""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from tokenrouter.catalog.refresh import apply_candidate, refresh
from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.config import Settings

# (id, input $/M, output $/M, context, tools, AA intelligence index or None)
MONTH_1 = [
    ("acme/spark-nano", 0.04, 0.15, 128_000, True, 24),
    ("acme/spark-mini", 0.10, 0.40, 1_000_000, True, 31),
    ("acme/spark", 0.30, 2.50, 1_000_000, True, 46),
    ("acme/spark-pro", 1.25, 10.00, 1_000_000, True, 61),
    ("orbit/haiku-x", 0.80, 4.00, 200_000, True, 38),
    ("orbit/sonnet-x", 3.00, 15.00, 200_000, True, 55),
    ("orbit/opus-x", 15.00, 75.00, 200_000, True, 66),
    ("lumen/l-small", 0.15, 0.60, 128_000, True, 29),
    ("lumen/l-medium", 0.40, 1.60, 400_000, True, 43),
    ("lumen/l-large", 2.00, 8.00, 400_000, True, 59),
    ("lumen/l-reason", 1.10, 4.40, 200_000, True, 57),
    ("nimbus/n1-chat", 0.07, 0.28, 64_000, False, 27),
    ("nimbus/n1-coder", 0.27, 1.10, 128_000, True, 41),
    ("nimbus/n1-think", 0.55, 2.19, 128_000, True, 56),
    ("quill/q-lite", 0.02, 0.06, 32_000, False, 19),
    ("quill/q-base", 0.20, 0.20, 32_768, True, 33),
    ("quill/q-max", 0.90, 0.90, 131_072, True, 47),
    ("quill/q-vision-preview", 0.50, 1.50, 128_000, True, None),   # no AA score -> unmatched
    ("acme/spark-experimental", 0.00001, 0.00001, 32_000, False, None),
]

# Next month: a cheaper equal-quality fast model appears, one price cut, one model retired.
MONTH_2_CHANGES = {
    "add": [("orbit/haiku-x2", 0.08, 0.32, 200_000, True, 32)],
    "price": {"lumen/l-large": (1.25, 5.00)},
    "remove": ["nimbus/n1-chat"],
}

SAMPLE_REQUESTS = [
    ("Tag each review as positive, negative or neutral: 'Battery is great', 'Screen cracked'.", "fast", 0.88),
    ("Extract every date from this invoice text: due 2026-11-02, issued Oct 1.", "fast", 0.81),
    ("Rewrite this sentence to be shorter: 'We are going to be holding a meeting tomorrow.'", "fast", 0.92),
    ("Summarise this 3-paragraph product update for a customer newsletter.", "balanced", 0.74),
    ("Answer from the attached docs: what is our refund window for annual plans?", "balanced", 0.69),
    ("Add input validation to this Flask endpoint and return 400 on bad JSON.", "balanced", 0.63),
    ("Classify this support ticket into billing, technical or sales.", "fast", 0.55),
    ("Plan a migration from a monolith to services for a 40-table schema, with rollback.", "reasoning", 0.77),
    ("Find the race condition in this async worker pool; it deadlocks under load.", "reasoning", 0.71),
    ("Compare three vendor proposals and recommend one with trade-offs.", "reasoning", 0.48),
    ("Draft a polite reply declining the meeting.", "fast", 0.83),
    ("Explain the difference between our two pricing tiers to a new customer.", "balanced", 0.58),
]


def _feed(models) -> tuple[dict, dict]:
    openrouter = {"data": [{
        "id": mid, "name": mid, "context_length": ctx,
        "architecture": {"output_modalities": ["text"]},
        "pricing": {"prompt": f"{inp / 1e6:.12f}", "completion": f"{out / 1e6:.12f}"},
        "supported_parameters": (["tools", "tool_choice"] if tools else []) + ["response_format", "temperature"],
    } for mid, inp, out, ctx, tools, _ in models]}
    aa = {"data": [{
        "slug": mid.split("/", 1)[1], "name": mid, "model_creator": {"name": mid.split("/")[0]},
        "evaluations": {"artificial_analysis_intelligence_index": q},
    } for mid, _, _, _, _, q in models if q is not None]}
    return openrouter, aa


def _month_2():
    models = [m for m in MONTH_1 if m[0] not in MONTH_2_CHANGES["remove"]]
    models = [(m[0], *MONTH_2_CHANGES["price"][m[0]], *m[3:]) if m[0] in MONTH_2_CHANGES["price"] else m
              for m in models]
    return models + MONTH_2_CHANGES["add"]


def demo_settings(base: Settings) -> Settings:
    root = base.data_dir / "demo"
    return replace(base, data_dir=root / "data", tiers_file=root / "tiers.yaml",
                   aliases_file=root / "aliases.yaml", auto_apply=False)


def build_demo(base: Settings, reset: bool = False) -> Settings:
    """Create (or reuse) the demo workspace and return settings pointing at it."""
    s = demo_settings(base)
    root = s.tiers_file.parent
    if reset and root.exists():
        shutil.rmtree(root)
    if s.tiers_file.exists():
        return s
    root.mkdir(parents=True, exist_ok=True)
    feeds = root / "feeds"
    feeds.mkdir(exist_ok=True)
    keys = {"OPENROUTER_API_KEY"}   # pretend: everything reachable via OpenRouter
    for i, (models, when) in enumerate(((MONTH_1, datetime(2026, 9, 1, 6, tzinfo=timezone.utc)),
                                        (_month_2(), datetime(2026, 10, 1, 6, tzinfo=timezone.utc)))):
        o, a = _feed(models)
        (feeds / f"openrouter-{i}.json").write_text(json.dumps(o))
        (feeds / f"aa-{i}.json").write_text(json.dumps(a))
        refresh(s, openrouter_file=feeds / f"openrouter-{i}.json", aa_file=feeds / f"aa-{i}.json",
                keys=keys, now=when)
    apply_candidate(s)
    seed_log(s)
    return s


def mock_table(table: TierTable, fail_ids: set[str] = frozenset(), answer: str | None = None) -> TierTable:
    """Copy of the tier table whose deployments answer with LiteLLM mock responses."""
    doc = json.loads(json.dumps(table.doc))
    for d in doc.get("model_list", []):
        dep_id = d["model_info"]["id"]
        params = d["litellm_params"]
        params.pop("api_base", None)
        params["api_key"] = "simulated"
        if dep_id in fail_ids:
            params["mock_response"] = Exception(f"simulated outage of {dep_id}")
        else:
            params["mock_response"] = answer or (
                f"[simulated answer from {params['model']} ({dep_id})] No model was called; "
                "switch execution to 'Real call' to get a real response.")
    return TierTable(doc)


def seed_log(s: Settings) -> None:
    """Run the sample requests through the real pipeline in shadow mode (simulated Jev and models)."""
    from tokenrouter.client import TokenRouter
    from tokenrouter.decision.jev import SimulatedJev
    from tokenrouter.execution.router import TierRouter
    from tokenrouter.log import DecisionLog

    table = mock_table(TierTable.load(s.tiers_file))
    log = DecisionLog(s.log_db)
    router = TierRouter(s, table)
    rng = random.Random(7)
    for prompt, choice, conf in SAMPLE_REQUESTS * 3:
        jitter = max(0.05, min(0.99, conf + rng.uniform(-0.15, 0.1)))
        tr = TokenRouter(s, mode="shadow", baseline_model=s.baseline_model, table=table, decision_log=log,
                         router=router, jev=SimulatedJev(choice, jitter, {"needs_tools": rng.random() * 0.4}),
                         baseline_kwargs={"mock_response": "[simulated baseline answer]", "api_key": "simulated"})
        tr.complete(prompt, project_id="demo", max_tokens=rng.choice([200, 400, 800]))
        tr.close(close_log=False)
    log.close()
