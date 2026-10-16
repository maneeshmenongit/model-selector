"""Router + client tests using LiteLLM's mock_response (no network)."""

from __future__ import annotations

import csv

import pytest

from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.client import HumanReviewRequired, TokenRouter
from tokenrouter.execution.router import RoutingError, TierRouter
from tokenrouter.log import DecisionLog
from tokenrouter.report import build_report, export_eval, import_eval, parse_grade


def dep(tier, rank, mock, in_cost=1e-6, out_cost=2e-6, ctx=200_000):
    return {"model_name": tier,
            "litellm_params": {"model": f"openai/{tier}-{rank}", "order": rank, "api_key": "test", "mock_response": mock},
            "model_info": {"id": f"{tier}-{rank}", "router_tier": tier, "rank": rank, "context_window": ctx,
                           "supports_tools": True, "usd_per_input_token": in_cost, "usd_per_output_token": out_cost}}


def table(fast1="fast-1 answer", fast2="fast-2 answer", balanced1="balanced answer"):
    return TierTable({
        "model_list": [dep("fast", 1, fast1, 1e-7, 4e-7), dep("fast", 2, fast2),
                       dep("balanced", 1, balanced1, 3e-7, 2.5e-6), dep("reasoning", 1, "reasoning answer", 5e-6, 2.5e-5)],
        "router_settings": {"fallbacks": [{"fast": ["balanced"]}, {"balanced": ["reasoning"]}]},
    })


def test_router_primary(settings):
    ex = TierRouter(settings, table()).complete("fast", [{"role": "user", "content": "hi"}])
    assert ex.response.choices[0].message.content == "fast-1 answer"
    assert (ex.model, ex.tier, ex.fallback_used) == ("openai/fast-1", "fast", False)
    assert ex.usd == pytest.approx(1e-7 * ex.prompt_tokens + 4e-7 * ex.completion_tokens)


def test_router_falls_back_within_tier_then_up(settings):
    ex = TierRouter(settings, table(fast1=Exception("down"))).complete("fast", [{"role": "user", "content": "hi"}])
    assert (ex.model, ex.fallback_used) == ("openai/fast-2", True)
    ex = TierRouter(settings, table(fast1=Exception("a"), fast2=Exception("b"))).complete(
        "fast", [{"role": "user", "content": "hi"}])
    assert (ex.model, ex.tier, ex.fallback_used) == ("openai/balanced-1", "balanced", True)


def test_router_all_down_raises_routing_error(settings):
    t = TierTable({"model_list": [dep("reasoning", 1, Exception("down"))], "router_settings": {"fallbacks": []}})
    with pytest.raises(RoutingError):
        TierRouter(settings, t).complete("reasoning", [{"role": "user", "content": "hi"}])


def _tr(settings, fake_jev, t=None, **kw):
    t = t or table()
    log = DecisionLog(settings.log_db)
    return TokenRouter(settings, table=t, jev=kw.pop("jev", fake_jev("fast", 0.9)), decision_log=log,
                       router=TierRouter(settings, t), **kw), log


def test_live_complete_logs_decision_and_cost(settings, fake_jev):
    tr, log = _tr(settings, fake_jev)
    res = tr.complete("Tag: I love it", project_id="default")
    assert res.text == "fast-1 answer" and res.tier == "fast"
    row = dict(log.conn.execute("SELECT * FROM requests WHERE id = ?", (res.request_id,)).fetchone())
    assert row["jev_version"] == "jev-1.13.0" and row["decision_source"] == "jev"
    assert row["model"] == "openai/fast-1" and row["primary_model"] == "openai/fast-1"
    assert row["usd"] == pytest.approx(res.usd) and row["usd"] > 0
    assert row["baseline_usd"] == pytest.approx(settings.baseline_cost(row["prompt_tokens"], row["completion_tokens"]))
    assert len(row["prompt_hash"]) == 16 and "love" not in str(row)
    assert row["decision_ms"] is not None and row["latency_ms"] >= row["decision_ms"]


def test_live_survives_jev_and_provider_outage(settings, fake_jev):
    tr, log = _tr(settings, fake_jev, t=table(balanced1=Exception("provider down")), jev=fake_jev(error="jev down"))
    res = tr.complete("hello")
    # Jev down -> balanced; balanced provider down -> falls back to reasoning.
    assert res.decision.source == "default" and res.model == "openai/reasoning-1" and res.fallback_used


def test_live_routing_error_is_logged_and_raised(settings, fake_jev):
    t = TierTable({"model_list": [dep("fast", 1, Exception("x")), dep("balanced", 1, Exception("y")),
                                  dep("reasoning", 1, Exception("z"))],
                   "router_settings": {"fallbacks": [{"fast": ["balanced"]}, {"balanced": ["reasoning"]}]}})
    tr, log = _tr(settings, fake_jev, t=t)
    with pytest.raises(RoutingError):
        tr.complete("hello")
    assert log.conn.execute("SELECT COUNT(*) FROM requests WHERE error IS NOT NULL").fetchone()[0] == 1


def test_human_review_is_queued(settings, fake_jev):
    tr, log = _tr(settings, fake_jev, jev=fake_jev("fast", 0.9, flags={"is_sensitive": 0.99}))
    with pytest.raises(HumanReviewRequired) as e:
        tr.complete("my medical records ...")
    q = log.conn.execute("SELECT * FROM review_queue WHERE request_id = ?", (e.value.request_id,)).fetchone()
    assert q["status"] == "open" and "medical" in q["messages"]


def test_shadow_mode_logs_estimate_and_eval_pairs(settings, fake_jev):
    tr, log = _tr(settings, fake_jev, mode="shadow", baseline_model="openai/baseline",
                  baseline_kwargs={"mock_response": "baseline answer", "api_key": "test"}, shadow_execute_rate=1.0)
    res = tr.complete("Tag: I love it")
    assert res.text == "baseline answer" and res.model == "openai/baseline" and res.tier == "fast"
    row = log.conn.execute("SELECT * FROM requests WHERE id = ?", (res.request_id,)).fetchone()
    assert row["mode"] == "shadow" and row["baseline_usd"] > row["routed_est_usd"] > 0
    pair = log.conn.execute("SELECT * FROM eval_pairs WHERE request_id = ?", (res.request_id,)).fetchone()
    assert (pair["baseline_output"], pair["routed_output"], pair["routed_tier"]) == \
        ("baseline answer", "fast-1 answer", "fast")


def test_shadow_decision_failure_never_breaks_baseline(settings, fake_jev):
    class Boom:
        def decide(self, state):
            raise RuntimeError("unexpected")
    tr, log = _tr(settings, fake_jev, jev=Boom(), mode="shadow", baseline_model="openai/baseline",
                  baseline_kwargs={"mock_response": "ok", "api_key": "test"})
    assert tr.complete("hi").text == "ok"


def test_report_and_grading_roundtrip(settings, fake_jev, tmp_path):
    tr, log = _tr(settings, fake_jev, mode="shadow", baseline_model="openai/baseline",
                  baseline_kwargs={"mock_response": "baseline answer", "api_key": "test"}, shadow_execute_rate=1.0)
    for i in range(4):
        tr.complete(f"Tag sentence {i}")
    out = tmp_path / "sample.csv"
    assert export_eval(log, out, n=10, seed=1) == 4
    rows = list(csv.DictReader(out.open()))
    for i, r in enumerate(rows):
        r["grade"] = "y" if i < 3 else "n"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    assert import_eval(log, out) == 4

    rep = build_report(log)
    assert rep.requests == 4 and rep.graded == 4 and rep.acceptable == 3
    assert rep.quality_loss == pytest.approx(0.25)
    assert rep.cost_reduction > 0.4
    text = rep.render()
    assert "[PASS] Router adds < 600 ms p95 overhead" in text
    assert "[PASS] >= 40% cost reduction" in text


def test_parse_grade():
    assert [parse_grade(v) for v in ("1", "Y", "no", "", "maybe")] == [1, 1, 0, None, None]


def test_budget_uses_logged_spend(make_settings, fake_jev):
    from tokenrouter import BudgetExceeded

    s = make_settings(projects={"default": {}, "tiny": {"monthly_budget_usd": 1e-9}})
    tr, log = _tr(s, fake_jev)
    tr.complete("first call is allowed", project_id="tiny")
    with pytest.raises(BudgetExceeded):
        tr.complete("second call is over the cap", project_id="tiny")


EMPTY_CUTOFF = {"choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": ""}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}


def test_empty_truncated_answer_falls_back_to_next_model(settings):
    ex = TierRouter(settings, table(fast1=EMPTY_CUTOFF)).complete("fast", [{"role": "user", "content": "hi"}])
    assert ex.response.choices[0].message.content == "fast-2 answer"
    assert (ex.deployment_id, ex.fallback_used) == ("fast-2", True)
    # The wasted call is still paid for.
    assert ex.usd > 1e-7 * 10 + 4e-7 * 20


def test_all_empty_truncated_raises_with_hint(settings):
    t = TierTable({"model_list": [dep("fast", 1, EMPTY_CUTOFF), dep("fast", 2, EMPTY_CUTOFF)],
                   "router_settings": {"fallbacks": []}})
    with pytest.raises(RoutingError, match="raise max_tokens"):
        TierRouter(settings, t).complete("fast", [{"role": "user", "content": "hi"}])
