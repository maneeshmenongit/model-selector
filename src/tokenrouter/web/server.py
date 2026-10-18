"""Local web app: watch how a request is routed and how the router behaves.

D5: ratified with scope frozen: no further UI work until the shadow run reports.

Standard library only (no new dependencies). Binds to 127.0.0.1. State-changing
endpoints require the `X-Tokenrouter: 1` header and a localhost Host header, so a
web page you visit can't trigger real (paid) model calls through it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from tokenrouter.catalog.join import NATIVE_PROVIDERS, OPENROUTER_KEY
from tokenrouter.catalog.store import CatalogStore
from tokenrouter.catalog.tiers_file import TierTable
from tokenrouter.config import HUMAN_REVIEW, Settings
from tokenrouter.decision.decider import Decider, Decision
from tokenrouter.decision.jev import FLAG_QUESTIONS, TIER_CRITERIA, JevClient, OfflineJev, SimulatedJev
from tokenrouter.decision.rules import BudgetExceeded
from tokenrouter.log import DecisionLog
from tokenrouter.report import build_report

log = logging.getLogger("tokenrouter.web")

KEY_NAMES = ["TYPESAFE_API_KEY", "ARTIFICIAL_ANALYSIS_API_KEY", OPENROUTER_KEY,
             *sorted({env for _, env in NATIVE_PROVIDERS.values()})]


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class App:
    def __init__(self, settings: Settings, demo: bool = False):
        self.s = settings
        self.demo = demo
        self.logs = {
            "requests": DecisionLog(settings.log_db),
            "playground": DecisionLog(settings.data_dir / "playground.db"),
        }
        self._table: TierTable | None = None
        self._table_mtime = 0.0
        self._real_router = None
        self._lock = threading.Lock()

    # -- shared -------------------------------------------------------------------

    def table(self) -> TierTable | None:
        if not self.s.tiers_file.exists():
            return None
        mtime = self.s.tiers_file.stat().st_mtime
        with self._lock:
            if self._table is None or mtime != self._table_mtime:
                self._table, self._table_mtime, self._real_router = TierTable.load(self.s.tiers_file), mtime, None
            return self._table

    def require_table(self) -> TierTable:
        t = self.table()
        if t is None:
            raise ApiError(HTTPStatus.CONFLICT, f"{self.s.tiers_file} not found - run `tokenrouter refresh` "
                                                "(or start with --demo)")
        return t

    # -- GET /api/state -----------------------------------------------------------

    def state(self) -> dict[str, Any]:
        table = self.table()
        tiers = []
        for name in self.s.tier_names:
            band = self.s.tiers[name]
            tiers.append({
                **asdict(band),
                "confidence_threshold": self.s.confidence_thresholds.get(name),
                "deployments": [asdict(d) | {"id": f"{d.tier}-{d.rank}"} for d in table.deployments(name)]
                if table else [],
            })
        return {
            "demo": self.demo,
            "tiers_file": str(self.s.tiers_file),
            "tiers_generated_at": table.doc.get("generated_at") if table else None,
            "has_tiers": table is not None,
            "tiers": tiers,
            "tier_criteria": TIER_CRITERIA,
            "flags": FLAG_QUESTIONS,
            "keys": {k: bool(os.environ.get(k)) for k in KEY_NAMES},
            "decision": {
                "jev_model": self.s.jev_model,
                "default_tier": self.s.default_tier,
                "flag_threshold": self.s.flag_threshold,
                "sensitive_threshold": self.s.sensitive_threshold,
                "long_context_tokens": self.s.long_context_tokens,
                "blocked_data_classes": sorted(self.s.blocked_data_classes),
                "max_output_tokens_default": self.s.max_output_tokens_default,
            },
            "projects": {pid: asdict(p) for pid, p in self.s.projects.items()} or {"default": {}},
            "blending": {"input_weight": self.s.input_weight, "output_weight": self.s.output_weight},
        }

    # -- GET /api/catalog ---------------------------------------------------------

    def catalog(self, refresh_id: int | None = None) -> dict[str, Any]:
        if not self.s.catalog_db.exists():
            return {"refreshes": [], "models": [], "unmatched": [], "diff": None}
        store = CatalogStore(self.s.catalog_db)
        try:
            refreshes = [dict(r) for r in store.conn.execute(
                "SELECT refresh_id, refreshed_at, n_models, n_unmatched, applied FROM refreshes "
                "ORDER BY refresh_id DESC")]
            if not refreshes:
                return {"refreshes": [], "models": [], "unmatched": [], "diff": None}
            rid = refresh_id or refreshes[0]["refresh_id"]
            rows = store.load(rid)
            unmatched_text = store.conn.execute(
                "SELECT unmatched FROM refreshes WHERE refresh_id = ?", (rid,)).fetchone()
        finally:
            store.close()
        models: dict[str, dict[str, Any]] = {}
        for r in rows:
            m = models.setdefault(r.model_id, {
                "model_id": r.model_id, "litellm_name": r.litellm_name, "provider": r.provider,
                "blended_price": r.blended_price, "input_usd_per_m": r.input_usd_per_m,
                "output_usd_per_m": r.output_usd_per_m, "quality": r.intelligence_idx,
                "coding_idx": r.coding_idx, "agentic_idx": r.agentic_idx,
                "quality_per_dollar": None if r.quality_per_dollar == float("inf") else r.quality_per_dollar,
                "context_window": r.context_window, "supports_tools": r.supports_tools,
                "supports_json": r.supports_json, "callable": r.callable, "native_key": r.native_key,
                "tiers": [],
            })
            if r.tier:
                m["tiers"].append({"tier": r.tier, "rank": r.rank_in_tier,
                                   "top": r.rank_in_tier is not None and r.rank_in_tier <= self.s.top_n})
        diff = None
        diffs = sorted(self.s.diffs_dir.glob(f"*-refresh-{rid}.md")) if self.s.diffs_dir.exists() else []
        if diffs:
            diff = diffs[-1].read_text()
        return {
            "refresh_id": rid,
            "refreshes": refreshes,
            "models": sorted(models.values(), key=lambda m: m["blended_price"]),
            "unmatched": [u for u in (unmatched_text[0] if unmatched_text else "").split("\n") if u],
            "diff": diff,
            "top_n": self.s.top_n,
        }

    # -- POST /api/route ----------------------------------------------------------

    def route(self, body: dict[str, Any]) -> dict[str, Any]:
        table = self.require_table()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise ApiError(HTTPStatus.BAD_REQUEST, "prompt is empty")
        project = body.get("project") or "default"
        data_classes = [c for c in body.get("data_classes") or [] if isinstance(c, str)]
        max_tokens = _int(body.get("max_tokens"), self.s.max_output_tokens_default, 1, 200_000)
        expected_out = _int(body.get("expected_output_tokens"), min(500, max_tokens), 0, 200_000)
        jev = self._jev(body.get("jev") or {})
        execution = body.get("execution") or {}
        mode = execution.get("mode", "dry")
        if mode == "real" and self.demo:
            raise ApiError(HTTPStatus.BAD_REQUEST, "real calls are off in demo mode: the demo models are fictional")
        messages = [{"role": "user", "content": prompt}]

        result: dict[str, Any] = {"execution": None, "error": None}
        if mode == "dry":
            decider = Decider(self.s, table, jev=jev, spend_lookup=self.logs["requests"].month_spend)
            try:
                decision = decider.decide(messages, project, data_classes, max_output_tokens=max_tokens)
            except BudgetExceeded as e:
                decision, result["error"] = e.decision, str(e)
        else:
            decision = self._execute(mode, table, jev, messages, project, data_classes, max_tokens,
                                     set(execution.get("fail") or []), result)

        result["decision"] = _decision_json(decision)
        result["costs"] = self._costs(table, project, decision.prompt_tokens, expected_out)
        result["expected_output_tokens"] = expected_out
        result["chain"] = self._chain(table, decision.tier)
        return result

    def _jev(self, cfg: dict[str, Any]):
        mode = cfg.get("mode", "simulate")
        if mode == "live":
            return JevClient(self.s.jev_model, timeout_s=self.s.jev_timeout_s)
        if mode == "offline":
            return OfflineJev()
        try:
            return SimulatedJev(cfg.get("choice", "balanced"), float(cfg.get("confidence", 0.8)),
                                {k: float(v) for k, v in (cfg.get("flags") or {}).items()})
        except (TypeError, ValueError) as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"bad simulated Jev settings: {e}") from e

    def _execute(self, mode, table, jev, messages, project, data_classes, max_tokens, fail, result) -> Decision:
        from tokenrouter.client import HumanReviewRequired, TokenRouter
        from tokenrouter.execution.router import RoutingError, TierRouter
        from tokenrouter.web.demo import mock_table

        if mode == "sim":
            run_table = mock_table(table, fail)
            router = TierRouter(self.s, run_table)
            target_log = self.logs["requests"] if self.demo else self.logs["playground"]
        elif mode == "real":
            run_table = table
            with self._lock:
                if self._real_router is None:
                    self._real_router = TierRouter(self.s, table)
                router = self._real_router
            target_log = self.logs["requests"]
        else:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown execution mode {mode!r}")

        tr = TokenRouter(self.s, table=run_table, jev=jev, decision_log=target_log, router=router,
                         log_mode="sim" if mode == "sim" else "live")
        try:
            res = tr.complete(messages, project_id=project, data_classes=data_classes, max_tokens=max_tokens)
            ex_info = {"status": "served", "request_id": res.request_id, "model": res.model, "tier": res.tier,
                       "fallback_used": res.fallback_used, "text": res.text, "usd": res.usd,
                       "latency_ms": res.latency_ms,
                       "deployment_id": (getattr(res.response, "_hidden_params", {}) or {}).get("model_id"),
                       "simulated": mode == "sim"}
            result["execution"] = ex_info
            return res.decision
        except HumanReviewRequired as e:
            result["execution"] = {"status": "human_review", "request_id": e.request_id, "reasons": e.reasons}
            return e.decision
        except BudgetExceeded as e:
            result["error"] = str(e)
            result["execution"] = {"status": "budget"}
            return e.decision
        except RoutingError as e:
            result["error"] = str(e)
            result["execution"] = {"status": "failed", "simulated": mode == "sim"}
            return e.decision
        finally:
            tr.close(close_log=False)

    def _costs(self, table: TierTable, project: str, prompt_tokens: int, out_tokens: int) -> dict[str, Any]:
        tiers = []
        for name in self.s.tier_names:
            p = table.primary(name)
            tiers.append({"tier": name, "model": p.litellm_name if p else None,
                          "usd": (p.input_cost_per_token * prompt_tokens + p.output_cost_per_token * out_tokens)
                          if p else None})
        b = self.s.baseline_for(project)
        return {"tiers": tiers, "baseline": {"model": b.model if b else None,
                                             "usd": b.cost(prompt_tokens, out_tokens) if b else None}}

    def _chain(self, table: TierTable, tier: str) -> list[dict[str, Any]]:
        """Order LiteLLM will try deployments in: the tier's members by rank, then each tier up."""
        if tier == HUMAN_REVIEW or tier not in self.s.tier_names:
            return []
        names = self.s.tier_names
        out = []
        for t in names[names.index(tier):]:
            out += [{"id": f"{d.tier}-{d.rank}", "tier": d.tier, "rank": d.rank, "model": d.litellm_name}
                    for d in table.deployments(t)]
        return out

    # -- GET /api/log, /api/report ----------------------------------------------------

    def _log(self, source: str) -> DecisionLog:
        if source not in self.logs:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown log {source!r}")
        return self.logs[source]

    def requests(self, source: str = "requests", limit: int = 100) -> dict[str, Any]:
        lg = self._log(source)
        rows = [dict(r) for r in lg.conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,))]
        for r in rows:
            for k in ("probabilities", "flags", "reasons"):
                r[k] = json.loads(r[k] or "null")
        return {"source": source, "rows": rows}

    def report(self, source: str = "requests") -> dict[str, Any]:
        rep = build_report(self._log(source))
        return {
            "source": source, "requests": rep.requests, "by_tier": rep.by_tier, "by_source": rep.by_source,
            "decision_p50_ms": rep.decision_p50_ms, "decision_p95_ms": rep.decision_p95_ms,
            "spend_usd": rep.spend_usd, "baseline_usd": rep.baseline_usd, "cost_reduction": rep.cost_reduction,
            "fallbacks": rep.fallbacks, "errors": rep.errors, "graded": rep.graded, "acceptable": rep.acceptable,
            "quality_loss": rep.quality_loss,
            "criteria": [{"name": n, "value": v, "ok": ok} for n, v, ok in rep.criteria()],
        }


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _decision_json(d: Decision) -> dict[str, Any]:
    return {
        "tier": d.tier, "source": d.source, "prompt_tokens": d.prompt_tokens, "reasons": d.reasons,
        "jev_version": d.jev_version, "jev_choice": d.jev_choice, "jev_confidence": d.jev_confidence,
        "probabilities": d.probabilities, "flags": d.flags, "flags_set": sorted(d.flags_set),
        "decision_ms": d.decision_ms, "trace": d.trace,
    }


class Handler(BaseHTTPRequestHandler):
    app: App  # set on the subclass made in serve()
    server_version = "tokenrouter"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad Host header"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                page = resources.files("tokenrouter.web").joinpath("static/index.html").read_bytes()
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self._json(HTTPStatus.OK, self.app.state())
            if url.path == "/api/catalog":
                rid = int(q["refresh_id"]) if q.get("refresh_id") else None
                return self._json(HTTPStatus.OK, self.app.catalog(rid))
            if url.path == "/api/log":
                return self._json(HTTPStatus.OK, self.app.requests(q.get("source", "requests"),
                                                                   _int(q.get("limit"), 100, 1, 1000)))
            if url.path == "/api/report":
                return self._json(HTTPStatus.OK, self.app.report(q.get("source", "requests")))
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ApiError as e:
            return self._json(e.status, {"error": str(e)})
        except Exception as e:  # keep the server up; report the error to the page
            log.exception("GET %s failed", self.path)
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{e.__class__.__name__}: {e}"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or self.headers.get("X-Tokenrouter") != "1":
            return self._json(HTTPStatus.FORBIDDEN, {"error": "missing X-Tokenrouter header or bad Host"})
        if urlparse(self.path).path != "/api/route":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 2_000_000)
            body = json.loads(self.rfile.read(length) or b"{}")
            return self._json(HTTPStatus.OK, self.app.route(body))
        except ApiError as e:
            return self._json(e.status, {"error": str(e)})
        except json.JSONDecodeError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "body is not JSON"})
        except Exception as e:
            log.exception("POST /api/route failed")
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{e.__class__.__name__}: {e}"})


def make_server(app: App, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((host, port), handler)


def serve(settings: Settings, demo: bool = False, host: str = "127.0.0.1", port: int = 8787,
          reset_demo: bool = False) -> None:
    if demo:
        from tokenrouter.web.demo import build_demo

        settings = build_demo(settings, reset=reset_demo)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    try:
        server = make_server(App(settings, demo=demo), host, port)
    except OSError as e:
        raise SystemExit(f"cannot listen on {host}:{port} ({e.strerror}); try --port <other>") from e
    print(f"tokenrouter web app on http://{host}:{port}  ({'DEMO workspace' if demo else settings.tiers_file})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
