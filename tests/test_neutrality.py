"""Provider-neutrality checks N-T1..N-T6 (phase1b section 1)."""

from __future__ import annotations

import random

import pytest

from tokenrouter import neutrality
from tokenrouter.catalog import tiering
from tokenrouter.catalog.diff import provider_share_lines
from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.tiering import assign_tiers, top_members
from tokenrouter.config import load_settings

VENDORS = ["anthropic", "openai", "google", "deepseek", "mistralai", "x-ai", "qwen", "z-ai", "acme"]


def synthetic_catalog(n: int = 200, seed: int = 11) -> list[CatalogRow]:
    """A catalog shaped like the real one, with exact ties and shared score entries mixed in."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        vendor = rng.choice(VENDORS)
        price = rng.choice([0.0, round(rng.uniform(0.01, 0.5), 3), round(rng.uniform(0.5, 4), 2),
                            round(rng.uniform(4, 30), 1)])
        rows.append(CatalogRow(
            model_id=f"{vendor}/model-{i}", provider=vendor,
            litellm_name=rng.choice([f"openrouter/{vendor}/model-{i}", f"{vendor}/model-{i}"]),
            input_usd_per_m=price, output_usd_per_m=price * 4, context_window=rng.choice([32_000, 128_000, 1_000_000]),
            supports_tools=rng.random() < 0.8, supports_json=rng.random() < 0.8,
            intelligence_idx=round(rng.uniform(10, 60), 1), aa_slug=f"model-{i}", blended_price=price,
            callable=rng.random() < 0.9, native_key=rng.random() < 0.4,
        ))
    # Exact ties on every field ranking reads, under different vendors and routes.
    for j in range(0, 20, 2):
        twin = rows[j + 1]
        rows[j + 1] = rows[j].with_(model_id=twin.model_id, provider=twin.provider, litellm_name=twin.litellm_name,
                                    aa_slug=f"twin-{j}", native_key=not rows[j].native_key)
    # Dated snapshots: same score entry and price, different ids.
    for j in range(20, 30):
        rows.append(rows[j].with_(model_id=f"{rows[j].model_id}-20260101", litellm_name=f"{rows[j].litellm_name}-20260101"))
    return rows


@pytest.fixture
def catalog():
    return synthetic_catalog()


_neutral_rank_key = tiering.rank_key


def _brand_biased_rank_key(row):
    """What N-T1/N-T2 exist to catch: a ranking that favours one vendor."""
    return (row.provider != "anthropic", *_neutral_rank_key(row))


def _id_biased_rank_key(row):
    return (not row.model_id.startswith("anthropic/"), *_neutral_rank_key(row))


def test_nt1_label_shuffle_leaves_tiers_unchanged(settings, catalog):
    assert neutrality.label_shuffle_check(catalog, settings) == []


def test_nt1_catches_a_ranking_that_reads_the_model_id(settings, catalog, monkeypatch):
    monkeypatch.setattr(tiering, "rank_key", _id_biased_rank_key)
    assert neutrality.label_shuffle_check(catalog, settings)


def test_nt2_provider_rename_leaves_tiers_and_ranks_unchanged(settings, catalog):
    assert neutrality.provider_rename_check(catalog, settings) == []


def test_nt2_catches_a_ranking_that_reads_the_provider(settings, catalog, monkeypatch):
    monkeypatch.setattr(tiering, "rank_key", _brand_biased_rank_key)
    assert neutrality.provider_rename_check(catalog, settings)


def test_nt1_nt2_on_the_real_catalog_snapshot():
    """Runs against the latest local refresh; the snapshot is not committed (it holds AA's data)."""
    s = load_settings()
    rows = neutrality.latest_catalog(s)
    if not rows:
        pytest.skip("no local catalog snapshot (run `tokenrouter refresh`)")
    assert neutrality.label_shuffle_check(rows, s) == []
    assert neutrality.provider_rename_check(rows, s) == []


def test_nt3_ranking_modules_hold_no_vendor_names():
    assert neutrality.static_check() == []


def test_nt3_flags_vendor_literals_and_label_reads(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('def rank(r):\n    # prefer Claude\n    return r.provider == "anthropic" or r.native_key\n')
    found = neutrality.static_check([bad])
    assert any("'anthropic'" in f for f in found)
    assert any("'claude'" in f for f in found)
    assert any(".provider" in f for f in found) and any(".native_key" in f for f in found)


def test_nt3_ignores_vendor_words_inside_other_words(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text('META = "metadata"\nX = "innovation tiling"\n')
    assert neutrality.static_check([ok]) == []


def test_nt3_tier_config_holds_no_vendor_names(make_settings):
    assert neutrality.tier_config_check({"tiers": {"fast": {"min_quality": 25}}}) == []
    assert neutrality.tier_config_check({"tiers": {"fast": {"prefer": "gemini"}}})


def test_nt4_extra_members_must_be_local(make_settings):
    ok = make_settings(extra_members={"fast": [{"litellm_name": "ollama/qwen3:8b"}]})
    assert neutrality.extra_members_check(ok) == []
    bad = make_settings(extra_members={"balanced": [{"litellm_name": "anthropic/claude-sonnet-5-5"}]})
    assert "anthropic/claude-sonnet-5-5" in neutrality.extra_members_check(bad)[0]
    via_gateway = make_settings(extra_members={"fast": [{"litellm_name": "openrouter/acme/x"}]})
    assert neutrality.extra_members_check(via_gateway)


def test_nt4_local_prefixes_are_configurable(make_settings):
    s = make_settings(catalog={"local_model_prefixes": ["ollama/", "lm_studio/"]},
                      extra_members={"fast": [{"litellm_name": "lm_studio/phi"}]})
    assert neutrality.extra_members_check(s) == []


def _row(mid, price, q, ctx=200_000, provider="p", native=False):
    return CatalogRow(model_id=mid, provider=provider, litellm_name=mid, input_usd_per_m=price,
                      output_usd_per_m=price, context_window=ctx, supports_tools=True, supports_json=True,
                      intelligence_idx=q, aa_slug=mid, blended_price=price, callable=True, native_key=native)


def _order(rows, settings, tier="balanced"):
    return [r.model_id for r in top_members(assign_tiers(rows, settings), tier, 10)]


def test_nt5_equal_quality_per_dollar_goes_to_the_lower_price(settings):
    rows = [_row("a/pricier", 2.0, 80), _row("b/cheaper", 1.0, 40)]
    assert _order(rows, settings) == ["b/cheaper", "a/pricier"]


def test_nt5_then_higher_score_then_larger_context(make_settings):
    s = make_settings(tiers={"fast": {"max_blended_price": None, "min_quality": 0}},
                      decision={"default_tier": "fast"})
    free = [_row("a/low", 0.0, 30), _row("b/high", 0.0, 50), _row("c/high-wide", 0.0, 50, ctx=1_000_000)]
    assert _order(free, s, "fast") == ["c/high-wide", "b/high", "a/low"]


def test_nt5_full_tie_is_broken_by_stable_id_hash_not_route_or_provider(settings):
    rows = [_row(f"v{i}/m", 1.0, 50, provider=f"v{i}", native=i % 2 == 0) for i in range(6)]
    by_hash = sorted((r.model_id for r in rows), key=neutrality.stable_hash)
    assert _order(rows, settings) == by_hash
    flipped = [r.with_(provider="anthropic", native_key=not r.native_key, litellm_name="openrouter/" + r.model_id)
               for r in rows]
    assert _order(flipped, settings) == by_hash
    assert neutrality.tie_break_check() == []


def test_nt6_provider_share_lists_each_member_with_its_evidence(settings, catalog):
    rows = assign_tiers(catalog, settings)
    s = settings
    s.extra_members = {"fast": [{"litellm_name": "ollama/qwen3:8b"}]}
    text = "\n".join(provider_share_lines(rows, s))
    for tier in s.tier_names:
        assert f"### {tier}" in text
    top = top_members(rows, "balanced", s.top_n)[0]
    assert top.model_id in text and f"{top.intelligence_idx:g}" in text and f"{top.quality_per_dollar:.1f}" in text
    assert "openrouter" in text and "direct" in text and "local" in text
    assert "ollama/qwen3:8b" in text
    assert "Providers:" in text and "Gateways:" in text


def test_run_all_reports_every_check(settings):
    results = neutrality.run_all(settings, rows=synthetic_catalog(), diff_text="## Provider share\n")
    assert [r.check for r in results] == ["N-T1", "N-T2", "N-T3", "N-T4", "N-T5", "N-T6"]
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_run_all_fails_nt6_when_the_diff_lacks_provider_share(settings):
    results = {r.check: r for r in neutrality.run_all(settings, rows=synthetic_catalog(), diff_text="# old diff\n")}
    assert not results["N-T6"].ok


def test_audit_command_fails_on_a_pinned_cloud_model(tmp_path, capsys):
    import yaml

    from tokenrouter.cli import main
    from tests.conftest import BASE_CONFIG

    cfg = {**BASE_CONFIG, "paths": {"data_dir": str(tmp_path / "data")},
           "extra_members": {"balanced": [{"litellm_name": "openrouter/acme/x"}]}}
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "tokenrouter.yaml"
    path.write_text(yaml.safe_dump(cfg))
    code = main(["--config", str(path), "audit"])
    out = capsys.readouterr().out
    assert code == 1
    assert "N-T4 FAIL" in out and "openrouter/acme/x" in out
    assert "N-T5 pass" in out


def test_nt3_scans_the_scoring_module():
    assert "tokenrouter.catalog.scoring" in neutrality.NEUTRAL_MODULES


def _vendor_soft_normalize(real):
    """A correction that goes easier on one vendor: what N2's 'identically' rule forbids."""
    def biased(rows, median_gap):
        rows = list(rows)
        soft = real([r for r in rows if r.provider == "anthropic"], median_gap / 2)
        rest = real([r for r in rows if r.provider != "anthropic"], median_gap)
        return soft + rest
    return biased


def test_nt1_nt2_run_the_whole_scoring_pipeline(make_settings, monkeypatch):
    from tokenrouter.catalog import scoring

    s = make_settings(catalog={"band_percentiles": {"fast": 50, "balanced": 75, "reasoning": 90}})
    rows = [r.with_(supports_reasoning=True, aa_score=r.intelligence_idx) for r in synthetic_catalog()]
    assert neutrality.label_shuffle_check(rows, s, median_gap=0.3) == []
    assert neutrality.provider_rename_check(rows, s, median_gap=0.3) == []
    monkeypatch.setattr(scoring, "normalize", _vendor_soft_normalize(scoring.normalize))
    assert neutrality.provider_rename_check(rows, s, median_gap=0.3)
