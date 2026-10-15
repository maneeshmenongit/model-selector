from __future__ import annotations

import json

import httpx
import pytest

from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.decision.decider import Decider
from tokenrouter.decision.jev import JEV_URL, JevClient, JevUnavailable, parse_response
from tokenrouter.decision.rules import BudgetExceeded, detect_data_classes


def _table(fast_ctx=40_000, balanced_ctx=200_000, reasoning_ctx=1_000_000, fast_tools=True):
    def dep(tier, ctx, tools=True):
        return {"model_name": tier, "litellm_params": {"model": f"x/{tier}", "order": 1},
                "model_info": {"id": f"{tier}-1", "rank": 1, "context_window": ctx, "supports_tools": tools}}
    return TierTable({"model_list": [dep("fast", fast_ctx, fast_tools), dep("balanced", balanced_ctx),
                                     dep("reasoning", reasoning_ctx)]})


MSG = [{"role": "user", "content": "Tag this sentence as positive or negative: I love it."}]


# -- Jev client -----------------------------------------------------------------------

JEV_OK = {
    "model": "jev-1.13.0",
    "answers": {
        "tier": {"type": "choice", "choice": "fast", "confidence": 0.82,
                 "probabilities": {"fast": 0.85, "balanced": 0.1, "reasoning": 0.04, "human_review": 0.01}},
        "needs_tools": {"type": "noul", "noul": 0.1},
        "needs_long_context": {"type": "noul", "noul": 0.02},
        "needs_current_info": {"type": "noul", "noul": 0.05},
        "is_sensitive": {"type": "noul", "noul": 0.01},
    },
    "usage": {"input_tokens": 400, "output_tokens": 20},
}


def test_jev_client_request_shape_and_parse():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json=JEV_OK)

    client = JevClient("jev-1.13.0", api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    ans = client.decide({"request": "hi"})
    assert seen["url"] == JEV_URL and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "jev-1.13.0"
    assert seen["body"]["questions"]["tier"]["type"] == "choice"
    assert set(seen["body"]["questions"]["tier"]["criteria"]) == {"fast", "balanced", "reasoning", "human_review"}
    assert seen["body"]["questions"]["is_sensitive"]["type"] == "noul"
    assert (ans.choice, ans.confidence, ans.model) == ("fast", 0.82, "jev-1.13.0")
    assert ans.flags["needs_tools"] == 0.1 and ans.input_tokens == 400


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(500, text="boom"),
    lambda req: httpx.Response(200, json={"answers": {}}),
    lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")),
])
def test_jev_client_failures_raise_unavailable(handler):
    client = JevClient(api_key="k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(JevUnavailable):
        client.decide("state")


def test_jev_client_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(JevUnavailable, match="TYPESAFE_API_KEY"):
        JevClient().decide("x")


def test_parse_response_rejects_unknown_choice():
    bad = json.loads(json.dumps(JEV_OK))
    bad["answers"]["tier"]["choice"] = "turbo"
    with pytest.raises(ValueError):
        parse_response(bad)


# -- hard rules -----------------------------------------------------------------------

def test_detect_secrets():
    assert detect_data_classes("key: AKIAABCDEFGHIJKLMNOP") == {"secrets"}
    assert detect_data_classes("-----BEGIN OPENSSH PRIVATE KEY-----") == {"secrets"}
    assert detect_data_classes("nothing to see") == set()


# -- decider --------------------------------------------------------------------------

def test_jev_choice_used(settings, fake_jev):
    d = Decider(settings, _table(), fake_jev("fast", 0.9)).decide(MSG)
    assert (d.tier, d.source, d.jev_version) == ("fast", "jev", "jev-1.13.0")


def test_jev_unavailable_defaults_to_balanced(settings, fake_jev):
    d = Decider(settings, _table(), fake_jev(error="down")).decide(MSG)
    assert (d.tier, d.source) == ("balanced", "default")


def test_low_confidence_escalates_one_tier(settings, fake_jev):
    d = Decider(settings, _table(), fake_jev("fast", 0.4)).decide(MSG)
    assert d.tier == "balanced" and "confidence" in d.reasons[0]


def test_low_confidence_reasoning_goes_to_human(settings, fake_jev):
    assert Decider(settings, _table(), fake_jev("reasoning", 0.2)).decide(MSG).tier == "human_review"


def test_sensitive_goes_to_human(settings, fake_jev):
    d = Decider(settings, _table(), fake_jev("fast", 0.9, flags={"is_sensitive": 0.95})).decide(MSG)
    assert d.tier == "human_review"


def test_blocked_data_class_skips_jev(settings, fake_jev):
    jev = fake_jev()
    d = Decider(settings, _table(), jev).decide(MSG, data_classes=["credentials"])
    assert d.tier == "human_review" and d.source == "rule" and not jev.states
    leaked = [{"role": "user", "content": "deploy with sk-ant-abcdefghijklmnopqrstuvwxyz123"}]
    assert Decider(settings, _table(), jev).decide(leaked).tier == "human_review"


def test_budget_cap(settings, fake_jev):
    dec = Decider(settings, _table(), fake_jev(), spend_lookup=lambda pid: 0.02)
    with pytest.raises(BudgetExceeded):
        dec.decide(MSG, project_id="capped")
    dec.decide(MSG, project_id="default")  # no cap


def test_context_floor_escalates(settings, fake_jev):
    long_msg = [{"role": "user", "content": "word " * 60_000}]   # ~60k tokens > fast's 40k
    d = Decider(settings, _table(), fake_jev("fast", 0.95)).decide(long_msg)
    assert d.tier == "balanced" and any("context" in r for r in d.reasons)


def test_nothing_fits_goes_to_human(settings, fake_jev):
    d = Decider(settings, _table(reasoning_ctx=100), fake_jev("reasoning", 0.9)).decide(MSG)
    assert d.tier == "human_review"


def test_inferred_long_context_is_soft(settings, fake_jev):
    # needs_long_context wants 200k; reasoning has 1M so it escalates there.
    d = Decider(settings, _table(), fake_jev("fast", 0.9, flags={"needs_long_context": 0.9})).decide(MSG)
    assert d.tier == "balanced"
    # If nothing has 200k, relax rather than send to human review.
    d = Decider(settings, _table(balanced_ctx=150_000, reasoning_ctx=150_000),
                fake_jev("fast", 0.9, flags={"needs_long_context": 0.9})).decide(MSG)
    assert d.tier == "fast" and "relaxed" in d.reasons[0]


def test_caller_tools_are_hard(settings, fake_jev):
    d = Decider(settings, _table(fast_tools=False), fake_jev("fast", 0.9)).decide(MSG, tools=[{"type": "function"}])
    assert d.tier == "balanced"


def test_private_project_sends_no_raw_prompt(settings, fake_jev):
    jev = fake_jev()
    Decider(settings, _table(), jev).decide(MSG, project_id="private", summary="sentiment tagging")
    assert jev.states[0]["request"] == "sentiment tagging"
    assert "I love it" not in json.dumps(jev.states[0])
    Decider(settings, _table(), jev).decide(MSG)
    assert "I love it" in jev.states[1]["request"]
    assert jev.states[1]["request_size"] == "short"
