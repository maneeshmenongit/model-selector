from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenrouter.config import settings_from_dict

BASE_CONFIG = {
    "catalog": {"top_n": 3, "auto_apply": False},
    "tiers": {
        "fast": {"max_blended_price": 0.50, "min_quality": 25, "requires": {"min_context": 32000}},
        "balanced": {"max_blended_price": 4.00, "min_quality": 40, "requires": {"min_context": 128000, "tools": True}},
        "reasoning": {"max_blended_price": None, "min_quality": 55, "requires": {"min_context": 128000, "tools": True}},
    },
    "decision": {
        "jev_model": "jev-1.13.0",
        "default_tier": "balanced",
        "confidence_thresholds": {"fast": 0.6, "balanced": 0.5, "reasoning": 0.3},
        "flag_threshold": 0.5,
        "sensitive_threshold": 0.8,
        "long_context_tokens": 200000,
        "blocked_data_classes": ["secrets", "credentials"],
    },
    "execution": {"num_retries": 0, "timeout_s": 10, "max_output_tokens_default": 1000},
    "projects": {
        "default": {"baseline": {"model": "openai/baseline", "input_usd_per_m": 3.0, "output_usd_per_m": 15.0}},
        "capped": {"monthly_budget_usd": 0.01},
        "private": {"send_raw_prompt": False},
        "agent-b": {"baseline": {"model": "openrouter/acme/agent-b-model", "input_usd_per_m": 0.5, "output_usd_per_m": 1.0}},
        "no-baseline": {"baseline": None},
    },
}


@pytest.fixture
def make_settings(tmp_path: Path):
    def _make(**overrides):
        raw = json.loads(json.dumps(BASE_CONFIG))
        for k, v in overrides.items():
            raw[k] = v
        raw["paths"] = {"data_dir": str(tmp_path / "data"), "tiers_file": str(tmp_path / "tiers.yaml"),
                        "aliases_file": str(tmp_path / "aliases.yaml")}
        return settings_from_dict(raw, root=tmp_path)
    return _make


@pytest.fixture
def settings(make_settings):
    return make_settings()


def or_model(mid, inp, out, ctx=200_000, tools=True, json_=True, modality=("text",), reasoning=False):
    """An OpenRouter /models entry; prices in USD per 1M tokens."""
    params = ((["tools", "tool_choice"] if tools else []) + (["response_format"] if json_ else []) + ["temperature"]
              + (["reasoning", "include_reasoning"] if reasoning else []))
    return {
        "id": mid,
        "name": mid,
        "context_length": ctx,
        "architecture": {"output_modalities": list(modality)},
        "pricing": {"prompt": f"{inp / 1e6:.12f}", "completion": f"{out / 1e6:.12f}"},
        "supported_parameters": params,
    }


def aa_model(slug, intel, coding=None, agentic=None, creator="X"):
    return {
        "slug": slug,
        "name": slug,
        "model_creator": {"name": creator},
        "evaluations": {
            "artificial_analysis_intelligence_index": intel,
            "artificial_analysis_coding_index": coding,
            "artificial_analysis_agentic_index": agentic,
        },
    }


@pytest.fixture
def openrouter_payload():
    return {"data": [
        or_model("google/gemini-9-flash-lite", 0.10, 0.40),          # blended 0.175, q 30 -> fast
        or_model("anthropic/claude-haiku-9", 1.00, 5.00),            # blended 2.0,   q 42 -> balanced
        or_model("google/gemini-9-flash", 0.30, 2.50),               # blended 0.85,  q 45 -> balanced
        or_model("anthropic/claude-opus-9", 5.00, 25.00),            # blended 10.0,  q 60 -> reasoning
        or_model("openai/gpt-9", 1.25, 10.00),                       # blended 3.4375, q 58 -> balanced+reasoning
        or_model("acme/no-score-model", 0.05, 0.10),                 # no AA match -> unmatched
        or_model("acme/tiny", 0.02, 0.02, ctx=8000),                 # too little context for fast
        or_model("acme/free-thing:free", 0, 0),                      # dropped (free variant)
        or_model("~acme/latest", 1, 1),                              # dropped (alias)
        or_model("acme/imagegen", 1, 1, modality=("image",)),        # dropped (no text output)
    ]}


@pytest.fixture
def aa_payload():
    return {"data": [
        aa_model("gemini-9-flash-lite", 30),
        aa_model("claude-haiku-9", 42),
        aa_model("gemini-9-flash", 45),
        aa_model("claude-opus-9", 60),
        aa_model("gpt-9", 58),
        aa_model("tiny", 26),
    ]}


@pytest.fixture
def price_map():
    return {
        "claude-haiku-9": {"mode": "chat", "litellm_provider": "anthropic"},
        "claude-opus-9": {"mode": "chat", "litellm_provider": "anthropic"},
        "gemini/gemini-9-flash": {"mode": "chat", "litellm_provider": "gemini"},
        "gemini/gemini-9-flash-lite": {"mode": "chat", "litellm_provider": "gemini"},
        "gpt-9": {"mode": "chat", "litellm_provider": "openai"},
        "text-embedding-9": {"mode": "embedding", "litellm_provider": "openai"},
    }


@pytest.fixture
def feed_files(tmp_path, openrouter_payload, aa_payload):
    o, a = tmp_path / "or.json", tmp_path / "aa.json"
    o.write_text(json.dumps(openrouter_payload))
    a.write_text(json.dumps(aa_payload))
    return o, a


class FakeJev:
    """Stands in for JevClient; returns a scripted answer or raises."""

    def __init__(self, choice="fast", confidence=0.9, flags=None, error=None):
        self.choice, self.confidence, self.flags, self.error = choice, confidence, flags or {}, error
        self.states = []

    def decide(self, state):
        from tokenrouter.decision.jev import JevAnswer, JevUnavailable

        self.states.append(state)
        if self.error:
            raise JevUnavailable(self.error)
        return JevAnswer(model="jev-1.13.0", choice=self.choice, confidence=self.confidence,
                         probabilities={self.choice: self.confidence}, flags=self.flags)


@pytest.fixture
def fake_jev():
    return FakeJev
