from __future__ import annotations

import yaml

from tokenrouter.catalog.diff import tier_diff
from tokenrouter.catalog.join import join, normalize
from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.refresh import apply_candidate, refresh
from tokenrouter.catalog.sources import parse_artificial_analysis, parse_openrouter
from tokenrouter.catalog.tiering import assign_tiers, top_members

ALL_KEYS = {"ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"}


def _rows(settings, openrouter_payload, aa_payload, price_map, keys=ALL_KEYS, aliases=None):
    j = join(parse_openrouter(openrouter_payload), parse_artificial_analysis(aa_payload),
             price_map, aliases or {}, keys)
    return j, assign_tiers(j.rows, settings)


def test_parse_openrouter_filters_and_converts(openrouter_payload):
    models = {m.model_id: m for m in parse_openrouter(openrouter_payload)}
    assert "acme/free-thing:free" not in models
    assert "~acme/latest" not in models
    assert "acme/imagegen" not in models
    m = models["google/gemini-9-flash-lite"]
    assert m.input_usd_per_m == 0.10 and m.output_usd_per_m == 0.40
    assert m.supports_tools and m.supports_json and m.context_window == 200_000


def test_parse_aa_reads_indices(aa_payload):
    m = parse_artificial_analysis(aa_payload)[0]
    assert (m.slug, m.intelligence_idx) == ("gemini-9-flash-lite", 30.0)


def test_normalize():
    assert normalize("google/Gemini-2.5-Flash_Lite:beta") == "gemini-2-5-flash-lite"
    assert normalize("anthropic/claude-opus-5.5") == normalize("claude-opus-5-5")


def test_join_blended_price_and_unmatched(settings, openrouter_payload, aa_payload, price_map):
    j, _ = _rows(settings, openrouter_payload, aa_payload, price_map)
    by_id = {r.model_id: r for r in j.rows}
    assert by_id["anthropic/claude-haiku-9"].blended_price == 0.75 * 1 + 0.25 * 5
    assert j.unmatched == ["acme/no-score-model"]


def test_join_prefers_native_key_else_openrouter(settings, openrouter_payload, aa_payload, price_map):
    j, _ = _rows(settings, openrouter_payload, aa_payload, price_map, keys={"GEMINI_API_KEY", "OPENROUTER_API_KEY"})
    by_id = {r.model_id: r for r in j.rows}
    assert by_id["google/gemini-9-flash"].litellm_name == "gemini/gemini-9-flash"
    assert by_id["google/gemini-9-flash"].native_key
    assert by_id["anthropic/claude-opus-9"].litellm_name == "openrouter/anthropic/claude-opus-9"
    assert by_id["anthropic/claude-opus-9"].callable and not by_id["anthropic/claude-opus-9"].native_key


def test_join_without_any_key_marks_uncallable(settings, openrouter_payload, aa_payload, price_map):
    j, rows = _rows(settings, openrouter_payload, aa_payload, price_map, keys=set())
    assert not any(r.callable for r in j.rows)
    assert all(r.rank_in_tier is None for r in rows)


def test_alias_map_overrides(settings, openrouter_payload, aa_payload, price_map):
    aliases = {"acme/no-score-model": {"aa_slug": "gemini-9-flash-lite"},
               "openai/gpt-9": {"skip": True}}
    j, _ = _rows(settings, openrouter_payload, aa_payload, price_map, aliases=aliases)
    ids = {r.model_id for r in j.rows}
    assert "acme/no-score-model" in ids and "openai/gpt-9" not in ids and not j.unmatched


def test_tiering_bands_ranking_and_overlap(settings, openrouter_payload, aa_payload, price_map):
    _, rows = _rows(settings, openrouter_payload, aa_payload, price_map)
    fast = [r.model_id for r in top_members(rows, "fast", 3)]
    balanced = [r.model_id for r in top_members(rows, "balanced", 3)]
    reasoning = [r.model_id for r in top_members(rows, "reasoning", 3)]
    assert fast == ["google/gemini-9-flash-lite"]               # tiny lacks context
    # quality per dollar: flash 45/0.85=52.9, haiku 42/2=21, gpt-9 58/3.44=16.9
    assert balanced == ["google/gemini-9-flash", "anthropic/claude-haiku-9", "openai/gpt-9"]
    assert reasoning == ["openai/gpt-9", "anthropic/claude-opus-9"]
    assert {r.tier for r in rows if r.model_id == "acme/tiny"} == {None}


def test_ranking_tie_goes_to_native_key(settings):
    base = dict(provider="p", input_usd_per_m=1, output_usd_per_m=1, context_window=200_000,
                supports_tools=True, supports_json=True, intelligence_idx=50, blended_price=1.0, callable=True)
    a = CatalogRow(model_id="a/x", litellm_name="openrouter/a/x", native_key=False, **base)
    b = CatalogRow(model_id="b/y", litellm_name="b/y", native_key=True, **base)
    ranked = top_members(assign_tiers([a, b], settings), "balanced", 3)
    assert [r.model_id for r in ranked] == ["b/y", "a/x"]


def _row(mid, tier, rank, price=1.0, q=50):
    return CatalogRow(model_id=mid, provider="p", litellm_name=mid, input_usd_per_m=price, output_usd_per_m=price,
                      context_window=1, supports_tools=True, supports_json=True, intelligence_idx=q,
                      blended_price=price, callable=True, tier=tier, rank_in_tier=rank)


def test_diff_lines():
    old = [_row("a", "fast", 1), _row("b", "fast", 2), _row("c", "fast", 3, price=1.0)]
    new = [_row("x", "fast", 1), _row("a", "fast", 2), _row("c", "fast", 3, price=1.5)]
    lines = tier_diff(old, new, ["fast"], 3)
    assert "fast #1: x (q=50, $1.000/M) replaces a" in lines
    assert "fast #2: a moves from #1" in lines
    assert "fast #3: c price $1.000 -> $1.500/M" in lines
    assert "fast: - b dropped out of the top 3" in lines


def test_refresh_end_to_end(settings, feed_files, openrouter_payload, aa_payload, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    o, a = feed_files
    keys = {"OPENROUTER_API_KEY"}
    first = refresh(settings, openrouter_file=o, aa_file=a, keys=keys)
    assert first.applied and settings.tiers_file.exists()     # first run auto-applies
    doc = yaml.safe_load(settings.tiers_file.read_text())
    fast = [d for d in doc["model_list"] if d["model_name"] == "fast"]
    assert fast[0]["litellm_params"] == {"model": "openrouter/google/gemini-9-flash-lite", "order": 1}
    assert doc["router_settings"]["fallbacks"] == [{"fast": ["balanced"]}, {"balanced": ["reasoning"]}]
    assert any("initial" in line for line in first.diff_lines)

    # Next month a cheaper equal-quality model appears: it is promoted, pending review.
    openrouter_payload["data"].append(
        {**openrouter_payload["data"][0], "id": "google/gemini-10-flash-lite",
         "pricing": {"prompt": "0.00000005", "completion": "0.0000002"}})
    aa_payload["data"].append({**aa_payload["data"][0], "slug": "gemini-10-flash-lite"})
    o.write_text(__import__("json").dumps(openrouter_payload))
    a.write_text(__import__("json").dumps(aa_payload))
    second = refresh(settings, openrouter_file=o, aa_file=a, keys=keys)
    assert not second.applied
    assert any("google/gemini-10-flash-lite" in l and "replaces google/gemini-9-flash-lite" in l
               for l in second.diff_lines)
    assert "pending review" in second.diff_path.read_text()
    live = yaml.safe_load(settings.tiers_file.read_text())
    assert live["refresh_id"] == first.refresh_id                # unchanged until apply
    assert apply_candidate(settings) == second.refresh_id
    assert yaml.safe_load(settings.tiers_file.read_text())["refresh_id"] == second.refresh_id


def test_refresh_adds_extra_members(make_settings, feed_files):
    s = make_settings(extra_members={"fast": [{"litellm_name": "ollama/qwen3:8b", "context_window": 40000,
                                               "litellm_params": {"api_base": "http://localhost:11434"}}]})
    o, a = feed_files
    refresh(s, openrouter_file=o, aa_file=a, keys={"OPENROUTER_API_KEY"})
    fast = [d for d in yaml.safe_load(s.tiers_file.read_text())["model_list"] if d["model_name"] == "fast"]
    assert [d["litellm_params"]["model"] for d in fast] == ["openrouter/google/gemini-9-flash-lite", "ollama/qwen3:8b"]
    assert fast[1]["litellm_params"]["order"] == 2 and fast[1]["model_info"]["extra_member"]


def test_native_index_matches_undated_names():
    from tokenrouter.catalog.join import build_native_index

    idx = build_native_index({"claude-sonnet-4-20250514": {"mode": "chat", "litellm_provider": "anthropic"}})
    assert idx[("anthropic", "claude-sonnet-4")] == "anthropic/claude-sonnet-4-20250514"
