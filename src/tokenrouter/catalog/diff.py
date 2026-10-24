"""Human-readable diff of tier membership between two refreshes."""

from __future__ import annotations

from collections import Counter

from tokenrouter.catalog.join import gateway
from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.tiering import top_members
from tokenrouter.config import Settings

PRICE_CHANGE_PCT = 10.0


def _label(r: CatalogRow) -> str:
    return f"{r.model_id} (q={r.intelligence_idx:g}, ${r.blended_price:.3f}/M)"


def tier_diff(
    old: list[CatalogRow] | None,
    new: list[CatalogRow],
    tiers: list[str],
    top_n: int,
) -> list[str]:
    """One line per change, e.g. 'fast #1: google/x replaces google/y'."""
    lines: list[str] = []
    for tier in tiers:
        new_top = top_members(new, tier, top_n)
        if not old:
            for r in new_top:
                lines.append(f"{tier} #{r.rank_in_tier}: {_label(r)} (initial)")
            if not new_top:
                lines.append(f"{tier}: EMPTY - no callable model qualifies")
            continue
        old_top = top_members(old, tier, top_n)
        old_ids = [r.model_id for r in old_top]
        new_ids = [r.model_id for r in new_top]
        old_by_id = {r.model_id: r for r in old_top}

        for i, r in enumerate(new_top):
            prev = old_ids[i] if i < len(old_ids) else None
            if prev == r.model_id:
                o = old_by_id[r.model_id]
                if o.blended_price and abs(r.blended_price - o.blended_price) / o.blended_price * 100 >= PRICE_CHANGE_PCT:
                    lines.append(
                        f"{tier} #{i + 1}: {r.model_id} price ${o.blended_price:.3f} -> ${r.blended_price:.3f}/M"
                    )
                continue
            if prev is None:
                lines.append(f"{tier} #{i + 1}: + {_label(r)}")
            elif r.model_id in old_ids:
                lines.append(f"{tier} #{i + 1}: {r.model_id} moves from #{old_ids.index(r.model_id) + 1}")
            else:
                lines.append(f"{tier} #{i + 1}: {_label(r)} replaces {prev}")
        for mid in old_ids:
            if mid not in new_ids:
                lines.append(f"{tier}: - {mid} dropped out of the top {top_n}")
        if not new_top:
            lines.append(f"{tier}: EMPTY - no callable model qualifies")
    return lines


def provider_share_lines(rows: list[CatalogRow], settings: Settings) -> list[str]:
    """N-T6: per tier, where each member comes from and the evidence that placed it."""
    out = ["Members per tier in rank order. Gateway: `openrouter` = called through OpenRouter, "
           "`direct` = the provider's own API, `local` = a local model. Score per $ = score / blended price.", ""]
    for tier in settings.tier_names:
        out += [f"### {tier}", "", "| # | Model | Provider | Gateway | Score | $/M blended | Score per $ |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        providers: Counter[str] = Counter()
        gateways: Counter[str] = Counter()
        members = top_members(rows, tier, settings.top_n)
        for r in members:
            gw = gateway(r.litellm_name, settings.local_model_prefixes)
            providers[r.provider] += 1
            gateways[gw] += 1
            basis = f" ({r.score_basis})" if r.score_basis else ""
            out.append(f"| {r.rank_in_tier} | {r.model_id} | {r.provider} | {gw} | {r.intelligence_idx:g}{basis} "
                       f"| {r.blended_price:.3f} | {r.quality_per_dollar:.1f} |")
        for i, extra in enumerate(settings.extra_members.get(tier, []), start=len(members) + 1):
            name = extra["litellm_name"]
            prov = extra.get("provider") or name.split("/", 1)[0]
            gw = gateway(name, settings.local_model_prefixes)
            providers[prov] += 1
            gateways[gw] += 1
            out.append(f"| {i} | {name} | {prov} | {gw} | - | 0 | - (extra member) |")
        if not members and not settings.extra_members.get(tier):
            out.append("| - | EMPTY | | | | | |")
        share = lambda c: ", ".join(f"{k} {v}" for k, v in c.most_common()) or "none"  # noqa: E731
        out += ["", f"Providers: {share(providers)}. Gateways: {share(gateways)}.", ""]
    return out


def scoring_lines(gap, derived, rows: list[CatalogRow], settings: Settings) -> list[str]:
    """N2 evidence: the effort gap, how each score was reached, and the derived bands."""
    basis = Counter(r.score_basis for r in {r.model_id: r for r in rows if r.callable}.values())
    out = ["## Effort normalization", "",
           f"Artificial Analysis's plain score is a model at maximum effort. {gap.count} families publish both a "
           f"plain and a medium score: median gap {gap.median:.1%} (p25 {gap.p25:.1%}, p75 {gap.p75:.1%}).",
           "",
           f"Callable models by score basis: {basis.get('measured', 0)} `measured` (AA's score at the effort we call at), "
           f"{basis.get('effort_unverified', 0)} `effort_unverified` (plain score x (1 - {gap.median:.1%}), "
           f"called with reasoning_effort=medium), {basis.get('no_effort_param', 0)} `no_effort_param` "
           f"(plain score kept: the model takes no reasoning parameter). The same rule applies to every provider.",
           ""]
    if gap.families:
        out += ["| Family | Plain (max) | Medium | Gap |", "| --- | --- | --- | --- |"]
        out += [f"| {f} | {p:g} | {m:g} | {g:.1%} |" for f, p, m, g in gap.families]
        out.append("")
    out += ["## Derived bands", ""]
    if derived.floors is None:
        out.append("Fixed quality floors from config (`catalog.band_percentiles` is not set).")
    else:
        out.append(f"Quality floor = a percentile of {derived.n_scores} callable models' normalized scores "
                   "(one per score entry). Price ceilings are unchanged.")
    out += ["", "| Tier | Percentile | Quality floor | Price ceiling ($/M) | Clear the floor, over the ceiling |",
            "| --- | --- | --- | --- | --- |"]
    for tier in settings.tier_names:
        b = derived.bands[tier]
        pct = f"p{settings.band_percentiles[tier]:g}" if derived.floors and tier in settings.band_percentiles else "fixed"
        ceiling = "none" if b.max_blended_price is None else f"{b.max_blended_price:.2f}"
        binds = derived.ceiling_excludes.get(tier)
        out.append(f"| {tier} | {pct} | {b.min_quality:g} | {ceiling} | "
                   f"{'-' if binds is None else (f'{binds} (ceiling binds)' if binds else '0')} |")
    return out + [""]


def render_markdown(
    refreshed_at: str,
    lines: list[str],
    unmatched: list[str],
    applied: bool,
    feed_notes: list[str] | None = None,
    provider_share: list[str] | None = None,
    scoring: list[str] | None = None,
) -> str:
    out = [f"# Tier refresh {refreshed_at}", ""]
    out.append("Status: **applied**" if applied else "Status: **pending review** - run `tokenrouter apply` to promote")
    out.append("")
    if feed_notes:
        out += ["## Feed notes", "", *[f"- {n}" for n in feed_notes], ""]
    if scoring:
        out += scoring
    out += ["## Tier changes", ""]
    out += [f"- {line}" for line in lines] if lines else ["- No changes"]
    if provider_share is not None:
        out += ["", "## Provider share", "", *provider_share]
    out += ["", f"## Unmatched models ({len(unmatched)})", ""]
    out.append("Not auto-tiered. Add entries to `config/aliases.yaml` to include them.")
    out.append("")
    out += [f"- {m}" for m in unmatched[:200]]
    if len(unmatched) > 200:
        out.append(f"- ... and {len(unmatched) - 200} more")
    return "\n".join(out) + "\n"
