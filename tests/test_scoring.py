"""N2: effort normalization (Q2) and percentile bands (D1)."""

from __future__ import annotations

import json

import pytest
import yaml

from tests.conftest import aa_model, or_model
from tokenrouter.catalog import scoring
from tokenrouter.catalog.join import join
from tokenrouter.catalog.models import CatalogRow
from tokenrouter.catalog.refresh import refresh
from tokenrouter.catalog.sources import parse_artificial_analysis, parse_openrouter
from tokenrouter.catalog.store import CatalogStore


def _aa(*models):
    return parse_artificial_analysis({"data": list(models)})


def test_effort_gap_uses_every_family_with_both_plain_and_medium():
    aa = _aa(aa_model("a-1", 50), aa_model("a-1-medium", 40),        # gap 20%
             aa_model("b-2", 60), aa_model("b-2-medium", 54),        # gap 10%
             aa_model("c-3", 40), aa_model("c-3-medium", 28),        # gap 30%
             aa_model("d-4", 45), aa_model("d-4-high", 44),          # no medium: ignored
             aa_model("e-5-medium", 30))                             # no plain: ignored
    gap = scoring.effort_gap(aa)
    assert gap.count == 3
    assert [f[0] for f in gap.families] == ["a-1", "b-2", "c-3"]
    assert gap.median == pytest.approx(0.20)
    assert gap.p25 == pytest.approx(0.15) and gap.p75 == pytest.approx(0.25)


def test_effort_gap_with_no_pairs_is_zero():
    gap = scoring.effort_gap(_aa(aa_model("x", 40)))
    assert gap.count == 0 and gap.median == 0.0


def _row(mid, slug, score, reasoning, provider="p", price=1.0, callable_=True):
    return CatalogRow(model_id=mid, provider=provider, litellm_name=mid, input_usd_per_m=price, output_usd_per_m=price,
                      context_window=200_000, supports_tools=True, supports_json=True, intelligence_idx=score,
                      aa_score=score, aa_slug=slug, blended_price=price, callable=callable_,
                      supports_reasoning=reasoning)


def test_normalize_plain_scores_by_the_median_gap_when_effort_is_settable():
    rows = scoring.normalize([_row("a/x", "x", 50.0, reasoning=True),
                              _row("b/y", "y", 50.0, reasoning=False),
                              _row("c/z", "z-medium", 40.0, reasoning=True)], median_gap=0.2)
    by = {r.model_id: r for r in rows}
    assert by["a/x"].intelligence_idx == pytest.approx(40.0) and by["a/x"].score_basis == "effort_unverified"
    assert by["a/x"].aa_score == 50.0
    assert by["b/y"].intelligence_idx == 50.0 and by["b/y"].score_basis == "no_effort_param"
    assert by["c/z"].intelligence_idx == 40.0 and by["c/z"].score_basis == "measured"


def test_normalize_is_identical_for_every_provider():
    rows = [_row(f"{p}/m", "m", 50.0, reasoning=True, provider=p) for p in ("anthropic", "google", "acme")]
    assert {r.intelligence_idx for r in scoring.normalize(rows, 0.25)} == {37.5}


def test_normalize_is_idempotent():
    once = scoring.normalize([_row("a/x", "x", 50.0, reasoning=True)], 0.2)
    assert scoring.normalize(once, 0.2)[0].intelligence_idx == pytest.approx(40.0)


def test_percentile_interpolates_linearly():
    assert scoring.percentile([10, 20, 30, 40], 50) == pytest.approx(25)
    assert scoring.percentile([10, 20, 30, 40], 90) == pytest.approx(37)
    assert scoring.percentile([7], 75) == 7


def test_bands_derive_from_callable_scores_once_per_score_entry(make_settings):
    s = make_settings(catalog={"band_percentiles": {"fast": 50, "balanced": 75, "reasoning": 90}})
    rows = [_row(f"v/m{i}", f"m{i}", float(q), reasoning=False) for i, q in enumerate([10, 20, 30, 40, 50])]
    rows += [_row("v/m4-snapshot", "m4", 50.0, reasoning=False),            # same score entry as m4: counted once
             _row("v/offline", "off", 99.0, reasoning=False, callable_=False)]  # not callable: not counted
    d = scoring.derive_bands(rows, s)
    assert d.n_scores == 5
    assert d.floors == pytest.approx({"fast": 30.0, "balanced": 40.0, "reasoning": 46.0})
    assert d.bands["balanced"].min_quality == pytest.approx(40.0)
    assert d.bands["balanced"].max_blended_price == s.tiers["balanced"].max_blended_price


def test_bands_report_which_price_ceilings_bind(make_settings):
    s = make_settings(catalog={"band_percentiles": {"fast": 50, "balanced": 75, "reasoning": 90}})
    rows = [_row("v/cheap", "a", 30.0, reasoning=False, price=0.1),
            _row("v/mid", "b", 40.0, reasoning=False, price=1.0),
            _row("v/pricey-fast", "c", 45.0, reasoning=False, price=2.0),   # clears fast floor (42.5), over $0.50
            _row("v/top", "d", 60.0, reasoning=False, price=9.0)]
    d = scoring.derive_bands(rows, s)
    assert d.ceiling_excludes["fast"] >= 1
    assert d.ceiling_excludes["reasoning"] == 0                              # no ceiling


def test_fixed_floors_when_no_percentiles_are_configured(settings):
    d = scoring.derive_bands([_row("v/a", "a", 30.0, reasoning=False)], settings)
    assert d.floors is None and d.bands == settings.tiers


def test_join_records_published_score_and_reasoning_support(price_map):
    o = {"data": [or_model("acme/thinker", 1, 1, reasoning=True), or_model("acme/plain", 1, 1)]}
    a = {"data": [aa_model("thinker", 50), aa_model("plain", 30)]}
    j = join(parse_openrouter(o), parse_artificial_analysis(a), price_map, {}, {"OPENROUTER_API_KEY"})
    by = {r.model_id: r for r in j.rows}
    assert by["acme/thinker"].supports_reasoning and not by["acme/plain"].supports_reasoning
    assert by["acme/thinker"].aa_score == 50


def test_store_round_trips_new_fields_and_migrates_old_dbs(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""CREATE TABLE refreshes (refresh_id INTEGER PRIMARY KEY AUTOINCREMENT, refreshed_at TEXT NOT NULL,
        n_models INTEGER NOT NULL, n_unmatched INTEGER NOT NULL, unmatched TEXT NOT NULL DEFAULT '',
        applied INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE catalog (refresh_id INTEGER NOT NULL, model_id TEXT NOT NULL, provider TEXT NOT NULL,
        litellm_name TEXT NOT NULL, input_usd_per_m REAL NOT NULL, output_usd_per_m REAL NOT NULL,
        context_window INTEGER NOT NULL, supports_tools INTEGER NOT NULL, supports_json INTEGER NOT NULL,
        intelligence_idx REAL, coding_idx REAL, agentic_idx REAL, aa_slug TEXT, blended_price REAL NOT NULL,
        callable INTEGER NOT NULL, native_key INTEGER NOT NULL, tier TEXT, rank_in_tier INTEGER,
        refreshed_at TEXT NOT NULL);""")
    conn.close()
    store = CatalogStore(db)
    row = scoring.normalize([_row("a/x", "x", 50.0, reasoning=True)], 0.2)[0]
    rid = store.save([row], [], "2026-10-24T00:00:00Z", effort_gap_median=0.2)
    back = store.load(rid)[0]
    assert (back.aa_score, back.supports_reasoning, back.score_basis) == (50.0, True, "effort_unverified")
    assert store.effort_gap_median(rid) == pytest.approx(0.2)
    store.close()


@pytest.fixture
def effort_feeds(tmp_path):
    o = {"data": [or_model("acme/flash", 0.1, 0.4, reasoning=True),        # plain only, effort settable
                  or_model("acme/basic", 0.1, 0.4),                       # plain only, no reasoning param
                  or_model("acme/pro", 1.0, 4.0, reasoning=True),         # has a medium variant
                  or_model("acme/max", 3.0, 12.0, reasoning=True)]}
    a = {"data": [aa_model("flash", 40), aa_model("basic", 30),
                  aa_model("pro", 60), aa_model("pro-medium", 48),         # gap 20%
                  aa_model("max", 70), aa_model("max-medium", 56),         # gap 20%
                  aa_model("other", 50), aa_model("other-medium", 40)]}    # gap 20%, not on OpenRouter
    of, af = tmp_path / "or.json", tmp_path / "aa.json"
    of.write_text(json.dumps(o))
    af.write_text(json.dumps(a))
    return of, af


def test_refresh_diff_reports_gap_bands_and_calls_at_medium(make_settings, effort_feeds):
    # fast at p0 so both plain-only models make the fast tier and their call params can be checked.
    s = make_settings(catalog={"band_percentiles": {"fast": 0, "balanced": 75, "reasoning": 90}, "top_n": 3})
    of, af = effort_feeds
    res = refresh(s, openrouter_file=of, aa_file=af, keys={"OPENROUTER_API_KEY"})
    text = res.diff_path.read_text()
    assert "## Effort normalization" in text and "3 families" in text and "median gap 20.0%" in text
    assert "## Derived bands" in text and "p0" in text and "p90" in text
    assert "effort_unverified" in text and "no_effort_param" in text
    doc = yaml.safe_load(s.candidate_tiers_file.read_text())
    deps = {d["model_info"]["model_id"]: d for d in doc["model_list"]}
    flash = deps["acme/flash"]
    assert flash["model_info"]["intelligence_idx"] == pytest.approx(32.0)          # 40 * (1 - 0.2)
    assert flash["model_info"]["score_basis"] == "effort_unverified"
    assert flash["litellm_params"]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in deps["acme/basic"]["litellm_params"]
