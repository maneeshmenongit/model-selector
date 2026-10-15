"""Monthly catalog refresh: fetch feeds, tier, store, diff, emit tiers.yaml."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from tokenrouter.catalog import sources
from tokenrouter.catalog.diff import render_markdown, tier_diff
from tokenrouter.catalog.join import NATIVE_PROVIDERS, OPENROUTER_KEY, join, load_aliases
from tokenrouter.catalog.store import CatalogStore
from tokenrouter.catalog.tiering import assign_tiers
from tokenrouter.catalog.tiers_file import build_tiers_doc, write_tiers_file
from tokenrouter.config import Settings


@dataclass
class RefreshResult:
    refresh_id: int
    refreshed_at: str
    diff_lines: list[str]
    unmatched: list[str]
    applied: bool
    diff_path: Path
    candidate_path: Path
    notes: list[str] = field(default_factory=list)


def available_keys() -> set[str]:
    names = {env for _, env in NATIVE_PROVIDERS.values()} | {OPENROUTER_KEY}
    return {n for n in names if os.environ.get(n)}


def refresh(
    settings: Settings,
    client: httpx.Client | None = None,
    openrouter_file: Path | None = None,
    aa_file: Path | None = None,
    keys: set[str] | None = None,
    now: datetime | None = None,
) -> RefreshResult:
    now = now or datetime.now(timezone.utc)
    refreshed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    keys = available_keys() if keys is None else keys
    notes: list[str] = []
    owns_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    try:
        if openrouter_file:
            openrouter = sources.parse_openrouter(json.loads(Path(openrouter_file).read_text()))
            notes.append(f"OpenRouter models read from {openrouter_file}")
        else:
            openrouter = sources.fetch_openrouter(client)
        if aa_file:
            aa = sources.parse_artificial_analysis(json.loads(Path(aa_file).read_text()))
            notes.append(f"Artificial Analysis scores read from {aa_file}")
        else:
            aa = sources.fetch_artificial_analysis(client)
        price_map = sources.fetch_litellm_price_map(None if openrouter_file else client)
    finally:
        if owns_client:
            client.close()

    notes.append(f"{len(openrouter)} OpenRouter models, {len(aa)} Artificial Analysis models")
    notes.append(f"Keys available: {', '.join(sorted(keys)) or 'none (only extra members are callable)'}")

    joined = join(
        openrouter, aa, price_map, load_aliases(settings.aliases_file), keys,
        settings.input_weight, settings.output_weight, refreshed_at,
    )
    rows = assign_tiers(joined.rows, settings)

    store = CatalogStore(settings.catalog_db)
    try:
        previous_id = store.latest_refresh_id(applied_only=True)
        previous = store.load(previous_id) if previous_id else None
        refresh_id = store.save(rows, joined.unmatched, refreshed_at)

        doc = build_tiers_doc(rows, settings, refreshed_at, refresh_id)
        write_tiers_file(doc, settings.candidate_tiers_file)

        # Nothing to review against on the first run, so apply it directly.
        apply_now = settings.auto_apply or not settings.tiers_file.exists()
        if not settings.tiers_file.exists():
            notes.append("First refresh: applied automatically (no live tiers.yaml existed)")
        if apply_now:
            _promote(settings, store, refresh_id)

        lines = tier_diff(previous, rows, settings.tier_names, settings.top_n)
        settings.diffs_dir.mkdir(parents=True, exist_ok=True)
        diff_path = settings.diffs_dir / f"{now.strftime('%Y-%m-%d')}-refresh-{refresh_id}.md"
        diff_path.write_text(render_markdown(refreshed_at, lines, joined.unmatched, apply_now, notes))
    finally:
        store.close()

    return RefreshResult(
        refresh_id=refresh_id,
        refreshed_at=refreshed_at,
        diff_lines=lines,
        unmatched=joined.unmatched,
        applied=apply_now,
        diff_path=diff_path,
        candidate_path=settings.candidate_tiers_file,
        notes=notes,
    )


def apply_candidate(settings: Settings) -> int:
    """Promote data/tiers.candidate.yaml to the live tiers file. Returns its refresh id."""
    import yaml

    if not settings.candidate_tiers_file.exists():
        raise FileNotFoundError("no candidate tiers file - run `tokenrouter refresh` first")
    refresh_id = (yaml.safe_load(settings.candidate_tiers_file.read_text()) or {}).get("refresh_id")
    store = CatalogStore(settings.catalog_db)
    try:
        _promote(settings, store, refresh_id)
    finally:
        store.close()
    return refresh_id


def _promote(settings: Settings, store: CatalogStore, refresh_id: int | None) -> None:
    settings.tiers_file.parent.mkdir(parents=True, exist_ok=True)
    if settings.tiers_file.exists():
        shutil.copy2(settings.tiers_file, settings.data_dir / "tiers.previous.yaml")
    shutil.copy2(settings.candidate_tiers_file, settings.tiers_file)
    if refresh_id is not None:
        store.mark_applied(refresh_id)
