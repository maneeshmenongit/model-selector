"""Human-readable diff of tier membership between two refreshes."""

from __future__ import annotations

from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.tiering import top_members

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


def render_markdown(
    refreshed_at: str,
    lines: list[str],
    unmatched: list[str],
    applied: bool,
    feed_notes: list[str] | None = None,
) -> str:
    out = [f"# Tier refresh {refreshed_at}", ""]
    out.append("Status: **applied**" if applied else "Status: **pending review** - run `tokenrouter apply` to promote")
    out.append("")
    if feed_notes:
        out += ["## Feed notes", "", *[f"- {n}" for n in feed_notes], ""]
    out += ["## Tier changes", ""]
    out += [f"- {line}" for line in lines] if lines else ["- No changes"]
    out += ["", f"## Unmatched models ({len(unmatched)})", ""]
    out.append("Not auto-tiered. Add entries to `config/aliases.yaml` to include them.")
    out.append("")
    out += [f"- {m}" for m in unmatched[:200]]
    if len(unmatched) > 200:
        out.append(f"- ... and {len(unmatched) - 200} more")
    return "\n".join(out) + "\n"
