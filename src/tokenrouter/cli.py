"""tokenrouter command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tokenrouter.config import load_settings


def cmd_refresh(args, s) -> int:
    from tokenrouter.catalog.refresh import refresh

    res = refresh(s, openrouter_file=args.openrouter_file, aa_file=args.aa_file)
    print(f"Refresh {res.refresh_id} at {res.refreshed_at}: {len(res.diff_lines)} tier changes, "
          f"{len(res.unmatched)} unmatched models")
    for note in res.notes:
        print(f"  note: {note}")
    for line in res.diff_lines:
        print(f"  {line}")
    print(f"Diff: {res.diff_path}")
    print("Applied to " + str(s.tiers_file) if res.applied else
          f"Candidate: {res.candidate_path} (run `tokenrouter apply` after review)")
    return 0


def cmd_apply(args, s) -> int:
    from tokenrouter.catalog.refresh import apply_candidate

    rid = apply_candidate(s)
    print(f"Applied refresh {rid} -> {s.tiers_file}")
    return 0


def cmd_tiers(args, s) -> int:
    from tokenrouter.catalog.tiers_file import TierTable

    table = TierTable.load(s.tiers_file)
    print(f"{s.tiers_file} (generated {table.doc.get('generated_at')})")
    for tier in s.tier_names:
        print(f"\n{tier}:")
        for d in table.deployments(tier):
            print(f"  #{d.rank} {d.litellm_name:55s} ${d.blended_price:.3f}/M  ctx {d.context_window:,}"
                  f"{'  tools' if d.supports_tools else ''}")
    return 0


def _router(s, args):
    from tokenrouter.client import TokenRouter
    from tokenrouter.decision.jev import JevClient

    return TokenRouter(s, jev=JevClient(args.jev_model or s.jev_model, timeout_s=s.jev_timeout_s))


def cmd_decide(args, s) -> int:
    from tokenrouter.catalog.tiers_file import TierTable
    from tokenrouter.decision.decider import Decider
    from tokenrouter.decision.jev import JevClient

    decider = Decider(s, TierTable.load(s.tiers_file), JevClient(args.jev_model or s.jev_model, timeout_s=s.jev_timeout_s))
    d = decider.decide([{"role": "user", "content": args.prompt}], args.project)
    print(json.dumps({
        "tier": d.tier, "source": d.source, "jev_version": d.jev_version, "jev_choice": d.jev_choice,
        "confidence": d.jev_confidence, "probabilities": d.probabilities, "flags": d.flags,
        "reasons": d.reasons, "prompt_tokens": d.prompt_tokens, "decision_ms": round(d.decision_ms, 1),
        "primary_model": (decider.table.primary(d.tier).litellm_name if decider.table.primary(d.tier) else None),
    }, indent=2))
    return 0


def cmd_ask(args, s) -> int:
    from tokenrouter.client import HumanReviewRequired

    tr = _router(s, args)
    try:
        res = tr.complete(args.prompt, project_id=args.project, max_tokens=args.max_tokens)
    except HumanReviewRequired as e:
        print(f"Queued for human review: {e}")
        return 2
    finally:
        tr.close()
    print(res.text)
    print(f"\n-- tier={res.tier} model={res.model} usd=${res.usd:.6f} latency={res.latency_ms:.0f}ms "
          f"fallback={res.fallback_used} source={res.decision.source}", file=sys.stderr)
    return 0


def cmd_report(args, s) -> int:
    from tokenrouter.log import DecisionLog
    from tokenrouter.report import build_report

    log = DecisionLog(s.log_db)
    print(build_report(log, args.project, args.since, args.mode).render())
    return 0


def cmd_eval(args, s) -> int:
    from tokenrouter.log import DecisionLog
    from tokenrouter.report import export_eval, import_eval

    log = DecisionLog(s.log_db)
    if args.eval_cmd == "export":
        n = export_eval(log, Path(args.out), args.n, args.seed)
        print(f"Exported {n} ungraded pairs to {args.out}; fill `grade` with 1/0 (or y/n) and import")
    else:
        n = import_eval(log, Path(args.file))
        print(f"Imported {n} grades")
    return 0


def cmd_audit(args, s) -> int:
    from tokenrouter.audit import audit

    problems = audit()
    for p in problems:
        print(f"PROBLEM: {p}")
    print("audit clean" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_serve(args, s) -> int:
    from tokenrouter.web.server import serve

    serve(s, demo=args.demo, host=args.host, port=args.port, reset_demo=args.reset_demo)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tokenrouter", description=__doc__)
    p.add_argument("--config", help="path to tokenrouter.yaml (default: $TOKENROUTER_CONFIG or config/)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("refresh", help="monthly catalog refresh: fetch feeds, re-rank tiers, write diff")
    r.add_argument("--openrouter-file", type=Path, help="read OpenRouter /models JSON from a file")
    r.add_argument("--aa-file", type=Path, help="read Artificial Analysis models JSON from a file")
    r.set_defaults(fn=cmd_refresh)

    sub.add_parser("apply", help="promote the reviewed candidate tiers file").set_defaults(fn=cmd_apply)
    sub.add_parser("tiers", help="show the live tiers").set_defaults(fn=cmd_tiers)

    for name, fn, help_ in (("decide", cmd_decide, "dry-run the tier decision for a prompt"),
                            ("ask", cmd_ask, "route a prompt and print the answer")):
        c = sub.add_parser(name, help=help_)
        c.add_argument("prompt")
        c.add_argument("--project", default="default")
        c.add_argument("--jev-model", help="override the pinned Jev model")
        if name == "ask":
            c.add_argument("--max-tokens", type=int, default=None)
        c.set_defaults(fn=fn)

    rep = sub.add_parser("report", help="cost / overhead / quality vs the Phase 1 exit criteria")
    rep.add_argument("--project")
    rep.add_argument("--since", help="ISO date, e.g. 2026-10-01")
    rep.add_argument("--mode", choices=["live", "shadow"])
    rep.set_defaults(fn=cmd_report)

    ev = sub.add_parser("eval", help="export / import shadow grading samples")
    evs = ev.add_subparsers(dest="eval_cmd", required=True)
    ex = evs.add_parser("export")
    ex.add_argument("--out", default="eval_sample.csv")
    ex.add_argument("-n", type=int, default=100)
    ex.add_argument("--seed", type=int)
    im = evs.add_parser("import")
    im.add_argument("file")
    ev.set_defaults(fn=cmd_eval)

    sub.add_parser("audit", help="check pinned versions and .pth files").set_defaults(fn=cmd_audit)

    sv = sub.add_parser("serve", help="local web app: watch routing decisions, tiers and the request log")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--host", default="127.0.0.1", help="keep on localhost; the app can make paid calls")
    sv.add_argument("--demo", action="store_true", help="use a demo workspace with fictional models")
    sv.add_argument("--reset-demo", action="store_true", help="rebuild the demo workspace")
    sv.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args, load_settings(args.config))


if __name__ == "__main__":
    sys.exit(main())
