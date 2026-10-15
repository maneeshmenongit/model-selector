from __future__ import annotations

import http.client
import json
import threading

import pytest

from tokenrouter.web.demo import build_demo
from tokenrouter.web.server import App, make_server


@pytest.fixture(scope="module")
def demo_app(tmp_path_factory):
    from tests.conftest import BASE_CONFIG
    from tokenrouter.config import settings_from_dict

    root = tmp_path_factory.mktemp("web")
    raw = json.loads(json.dumps(BASE_CONFIG))
    raw["paths"] = {"data_dir": str(root / "data"), "tiers_file": str(root / "tiers.yaml"),
                    "aliases_file": str(root / "aliases.yaml")}
    raw["projects"]["demo"] = {}
    s = build_demo(settings_from_dict(raw, root=root))
    return App(s, demo=True)


def route(app, **kw):
    body = {"prompt": "Tag this review: great battery", "project": "default",
            "jev": {"mode": "simulate", "choice": "fast", "confidence": 0.9}, "execution": {"mode": "dry"}}
    body.update(kw)
    return app.route(body)


def test_demo_workspace_has_tiers_catalog_and_seeded_log(demo_app):
    st = demo_app.state()
    assert st["demo"] and st["has_tiers"]
    assert [t["name"] for t in st["tiers"]] == ["fast", "balanced", "reasoning"]
    assert all(t["deployments"] for t in st["tiers"])
    cat = demo_app.catalog()
    assert len(cat["refreshes"]) == 2 and cat["models"] and cat["unmatched"]
    assert "replaces" in cat["diff"]
    assert demo_app.report()["requests"] == 36


def test_dry_run_trace_has_every_stage(demo_app):
    res = route(demo_app)
    d = res["decision"]
    assert d["tier"] == "fast"
    assert [s["step"] for s in d["trace"]] == ["hard_rules", "jev", "escalation", "context_floor"]
    assert res["execution"] is None
    assert res["chain"][0]["id"] == "fast-1" and res["chain"][-1]["tier"] == "reasoning"
    costs = {c["tier"]: c["usd"] for c in res["costs"]["tiers"]}
    assert costs["fast"] < costs["balanced"] < res["costs"]["baseline"]["usd"]


def test_low_confidence_escalation_in_trace(demo_app):
    d = route(demo_app, jev={"mode": "simulate", "choice": "fast", "confidence": 0.3})["decision"]
    esc = next(s for s in d["trace"] if s["step"] == "escalation")
    assert esc["outcome"] == "changed" and d["tier"] == "balanced"


def test_offline_jev_and_blocked_secret(demo_app):
    d = route(demo_app, jev={"mode": "offline"})["decision"]
    assert d["source"] == "default" and d["tier"] == "balanced"
    d = route(demo_app, prompt="use key AKIAABCDEFGHIJKLMNOP")["decision"]
    assert d["tier"] == "human_review" and d["trace"][0]["outcome"] == "stop"


def test_simulated_execution_with_outage_falls_back(demo_app):
    res = route(demo_app, execution={"mode": "sim", "fail": ["fast-1", "fast-2"]})
    ex = res["execution"]
    assert ex["status"] == "served" and ex["deployment_id"] == "fast-3" and ex["fallback_used"] and ex["simulated"]
    row = demo_app.logs["requests"].conn.execute("SELECT mode FROM requests WHERE id = ?", (ex["request_id"],)).fetchone()
    assert row["mode"] == "sim"


def test_simulated_total_outage_reports_failure(demo_app):
    ids = [d["id"] for t in demo_app.state()["tiers"] for d in t["deployments"]]
    res = route(demo_app, execution={"mode": "sim", "fail": ids})
    assert res["execution"]["status"] == "failed" and res["error"]
    assert res["decision"]["trace"]


def test_human_review_execution(demo_app):
    res = route(demo_app, jev={"mode": "simulate", "choice": "balanced", "confidence": 0.9,
                               "flags": {"is_sensitive": 0.95}}, execution={"mode": "sim"})
    assert res["execution"]["status"] == "human_review" and res["decision"]["tier"] == "human_review"


def test_real_calls_refused_in_demo(demo_app):
    from tokenrouter.web.server import ApiError

    with pytest.raises(ApiError):
        route(demo_app, execution={"mode": "real"})


def test_http_server_guards(demo_app):
    server = make_server(demo_app, port=0)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        def req(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            c.request(method, path, body=json.dumps(body) if body else None, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read()

        status, page = req("GET", "/")
        assert status == 200 and b"<title>tokenrouter</title>" in page
        assert req("GET", "/api/state")[0] == 200
        body = {"prompt": "hi", "jev": {"mode": "simulate", "choice": "fast", "confidence": 0.9}}
        assert req("POST", "/api/route", body, {"Content-Type": "application/json"})[0] == 403   # no header
        assert req("POST", "/api/route", body, {"X-Tokenrouter": "1", "Host": "evil.example"})[0] == 403
        status, out = req("POST", "/api/route", body, {"X-Tokenrouter": "1"})
        assert status == 200 and json.loads(out)["decision"]["tier"] == "fast"
        assert req("GET", "/api/state", headers={"Host": "attacker.test"})[0] == 403
    finally:
        server.shutdown()
