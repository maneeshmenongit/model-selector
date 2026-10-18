"""Phase 1 report: cost, overhead, fallbacks and grading against the exit criteria."""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from tokenrouter.log import DecisionLog

TARGET_P95_MS = 600.0
TARGET_COST_REDUCTION = 0.40
TARGET_MAX_QUALITY_LOSS = 0.05
TARGET_GRADED = 100


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class Report:
    requests: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    decision_p50_ms: float | None = None
    decision_p95_ms: float | None = None
    spend_usd: float = 0.0          # live: actual; shadow: estimated routed cost
    baseline_usd: float = 0.0
    fallbacks: int = 0
    errors: int = 0
    graded: int = 0
    acceptable: int = 0

    @property
    def cost_reduction(self) -> float | None:
        return 1 - self.spend_usd / self.baseline_usd if self.baseline_usd else None

    @property
    def quality_loss(self) -> float | None:
        return 1 - self.acceptable / self.graded if self.graded else None

    def criteria(self) -> list[tuple[str, str, bool | None]]:
        def fmt(v, f):
            return "n/a" if v is None else f.format(v)

        p95, red, loss = self.decision_p95_ms, self.cost_reduction, self.quality_loss
        return [
            ("Router adds < 600 ms p95 overhead", fmt(p95, "{:.0f} ms"),
             None if p95 is None else p95 < TARGET_P95_MS),
            (">= 40% cost reduction vs each agent's current model", fmt(red, "{:.1%}"),
             None if red is None else red >= TARGET_COST_REDUCTION),
            (f"Quality loss <= 5% on graded sample ({self.graded}/{TARGET_GRADED} graded)", fmt(loss, "{:.1%}"),
             None if loss is None or self.graded < TARGET_GRADED else loss <= TARGET_MAX_QUALITY_LOSS),
            ("Failures recorded (handled; unhandled ones never reach the log)", str(self.errors),
             None if self.requests == 0 else self.errors == 0),
        ]

    def render(self) -> str:
        lines = [
            f"Requests: {self.requests}",
            f"Tiers: {json.dumps(self.by_tier)}",
            f"Decision source: {json.dumps(self.by_source)}",
            f"Decision overhead: p50 {self._ms(self.decision_p50_ms)}, p95 {self._ms(self.decision_p95_ms)}",
            f"Spend: ${self.spend_usd:.4f} vs baseline ${self.baseline_usd:.4f}",
            f"Fallbacks used: {self.fallbacks}",
            "",
            "Exit criteria:",
        ]
        for name, value, ok in self.criteria():
            mark = "PASS" if ok else ("----" if ok is None else "FAIL")
            lines.append(f"  [{mark}] {name}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _ms(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.0f} ms"


def build_report(log: DecisionLog, project_id: str | None = None, since: str | None = None,
                 mode: str | None = None) -> Report:
    where, args = ["1=1"], []
    if project_id:
        where.append("project_id = ?"); args.append(project_id)
    if since:
        where.append("ts >= ?"); args.append(since)
    if mode:
        where.append("mode = ?"); args.append(mode)
    rows = log.conn.execute(f"SELECT * FROM requests WHERE {' AND '.join(where)}", args).fetchall()

    r = Report(requests=len(rows))
    decision_ms = []
    for row in rows:
        r.by_tier[row["tier"] or "?"] = r.by_tier.get(row["tier"] or "?", 0) + 1
        r.by_source[row["decision_source"] or "?"] = r.by_source.get(row["decision_source"] or "?", 0) + 1
        if row["decision_ms"] is not None:
            decision_ms.append(row["decision_ms"])
        r.fallbacks += row["fallback_used"]
        r.errors += row["error"] is not None
        if row["baseline_usd"] is None:
            continue
        spend = row["routed_est_usd"] if row["mode"] == "shadow" else row["usd"]
        if spend is not None:
            r.spend_usd += spend
            r.baseline_usd += row["baseline_usd"]
    r.decision_p50_ms = percentile(decision_ms, 50)
    r.decision_p95_ms = percentile(decision_ms, 95)

    ids = [row["id"] for row in rows]
    if ids:
        q = f"SELECT grade FROM eval_pairs WHERE grade IS NOT NULL AND request_id IN ({','.join('?' * len(ids))})"
        grades = [g[0] for g in log.conn.execute(q, ids)]
        r.graded, r.acceptable = len(grades), sum(grades)
    return r


# -- grading -------------------------------------------------------------------------

EXPORT_FIELDS = ["request_id", "routed_tier", "routed_model", "baseline_model",
                 "prompt", "baseline_output", "routed_output", "grade", "grader_note"]


def export_eval(log: DecisionLog, out: Path, n: int = 100, seed: int | None = None) -> int:
    rows = [dict(r) for r in log.conn.execute("SELECT * FROM eval_pairs WHERE grade IS NULL")]
    random.Random(seed).shuffle(rows)
    rows = rows[:n]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def parse_grade(value: str) -> int | None:
    v = (value or "").strip().lower()
    if v in ("1", "y", "yes", "ok", "pass", "true"):
        return 1
    if v in ("0", "n", "no", "fail", "false"):
        return 0
    return None


def import_eval(log: DecisionLog, path: Path) -> int:
    n = 0
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            g = parse_grade(row.get("grade", ""))
            if g is not None:
                log.grade(int(row["request_id"]), g, row.get("grader_note") or None)
                n += 1
    return n
