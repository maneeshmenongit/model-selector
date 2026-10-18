"""Load tokenrouter.yaml into typed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TIER_ORDER = ("fast", "balanced", "reasoning")
HUMAN_REVIEW = "human_review"

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "tokenrouter.yaml"


@dataclass(frozen=True)
class TierBand:
    name: str
    max_blended_price: float | None
    min_quality: float
    min_context: int = 0
    tools: bool = False
    json: bool = False


@dataclass(frozen=True)
class Baseline:
    """The model an agent uses today: what cost reduction is measured against (Q3)."""
    model: str
    input_usd_per_m: float
    output_usd_per_m: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (self.input_usd_per_m * prompt_tokens + self.output_usd_per_m * completion_tokens) / 1e6


@dataclass(frozen=True)
class ProjectSettings:
    monthly_budget_usd: float | None = None
    send_raw_prompt: bool = True
    baseline: Baseline | None = None


@dataclass
class Settings:
    root: Path
    data_dir: Path
    tiers_file: Path
    aliases_file: Path
    input_weight: float = 0.75
    output_weight: float = 0.25
    top_n: int = 3
    auto_apply: bool = False
    score_effort_preference: tuple[str, ...] = ()
    exclusive_tiers: bool = False
    tiers: dict[str, TierBand] = field(default_factory=dict)
    extra_members: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    local_model_prefixes: tuple[str, ...] = ("ollama/",)   # the only models `extra_members` may pin (N-T4)
    config_path: Path | None = None
    jev_model: str = "jev-1.13.0"
    jev_timeout_s: float = 2.0
    default_tier: str = "balanced"
    confidence_thresholds: dict[str, float] = field(default_factory=dict)
    flag_threshold: float = 0.5
    sensitive_threshold: float = 0.8
    long_context_tokens: int = 200_000
    state_prompt_chars: int = 4000
    blocked_data_classes: frozenset[str] = frozenset()
    num_retries: int = 1
    timeout_s: float = 120
    max_output_tokens_default: int = 4096
    projects: dict[str, ProjectSettings] = field(default_factory=dict)

    def baseline_for(self, project_id: str) -> Baseline | None:
        """The agent's recorded baseline; None until one is recorded (no default model)."""
        return self.project(project_id).baseline

    @property
    def tier_names(self) -> list[str]:
        return [t for t in TIER_ORDER if t in self.tiers]

    def project(self, project_id: str) -> ProjectSettings:
        return self.projects.get(project_id) or self.projects.get("default") or ProjectSettings()

    def next_tier(self, tier: str) -> str:
        """One tier up; reasoning escalates to human_review."""
        names = self.tier_names
        if tier not in names:
            return HUMAN_REVIEW
        i = names.index(tier)
        return names[i + 1] if i + 1 < len(names) else HUMAN_REVIEW

    @property
    def catalog_db(self) -> Path:
        return self.data_dir / "catalog.db"

    @property
    def log_db(self) -> Path:
        return self.data_dir / "decisions.db"

    @property
    def candidate_tiers_file(self) -> Path:
        return self.data_dir / "tiers.candidate.yaml"

    @property
    def diffs_dir(self) -> Path:
        return self.data_dir / "diffs"


# D8: ratified by the design thread (keys from the environment or .env).
def load_dotenv(path: str | os.PathLike) -> list[str]:
    """Load KEY=value lines into os.environ without overriding variables already set.

    Returns the names it set. Handles `export KEY=...`, quotes and # comments.
    """
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key.isidentifier() and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _resolve(root: Path, p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else root / path


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    cfg_path = Path(path or os.environ.get("TOKENROUTER_CONFIG") or DEFAULT_CONFIG).resolve()
    raw = yaml.safe_load(cfg_path.read_text()) or {}
    s = settings_from_dict(raw, root=cfg_path.parent.parent)
    s.config_path = cfg_path
    return s


def _baseline(raw: dict[str, Any] | None) -> Baseline | None:
    if not raw:
        return None
    return Baseline(model=raw["model"], input_usd_per_m=float(raw["input_usd_per_m"]),
                    output_usd_per_m=float(raw["output_usd_per_m"]))


def settings_from_dict(raw: dict[str, Any], root: Path) -> Settings:
    paths = raw.get("paths", {})
    catalog = raw.get("catalog", {})
    decision = raw.get("decision", {})
    execution = raw.get("execution", {})
    if "baseline" in raw:
        raise ValueError("top-level `baseline:` is gone: record each agent's current model under "
                         "projects.<project_id>.baseline (model, input_usd_per_m, output_usd_per_m)")

    tiers: dict[str, TierBand] = {}
    for name, band in (raw.get("tiers") or {}).items():
        if name not in TIER_ORDER:
            raise ValueError(f"unknown tier {name!r}; expected one of {TIER_ORDER}")
        req = band.get("requires") or {}
        tiers[name] = TierBand(
            name=name,
            max_blended_price=band.get("max_blended_price"),
            min_quality=float(band.get("min_quality", 0)),
            min_context=int(req.get("min_context", 0)),
            tools=bool(req.get("tools", False)),
            json=bool(req.get("json", False)),
        )

    projects = {
        pid: ProjectSettings(
            monthly_budget_usd=p.get("monthly_budget_usd"),
            send_raw_prompt=p.get("send_raw_prompt", True),
            baseline=_baseline(p.get("baseline")),
        )
        for pid, p in (raw.get("projects") or {}).items()
    }

    s = Settings(
        root=root,
        data_dir=_resolve(root, paths.get("data_dir", "data")),
        tiers_file=_resolve(root, paths.get("tiers_file", "config/tiers.yaml")),
        aliases_file=_resolve(root, paths.get("aliases_file", "config/aliases.yaml")),
        input_weight=catalog.get("input_weight", 0.75),
        output_weight=catalog.get("output_weight", 0.25),
        top_n=catalog.get("top_n", 3),
        auto_apply=catalog.get("auto_apply", False),
        score_effort_preference=tuple(catalog.get("score_effort_preference") or ()),
        exclusive_tiers=bool(catalog.get("exclusive_tiers", False)),
        tiers=tiers,
        extra_members=raw.get("extra_members") or {},
        local_model_prefixes=tuple(catalog.get("local_model_prefixes") or ("ollama/",)),
        jev_model=decision.get("jev_model", "jev-1.13.0"),
        jev_timeout_s=decision.get("jev_timeout_s", 2.0),
        default_tier=decision.get("default_tier", "balanced"),
        confidence_thresholds=decision.get("confidence_thresholds") or {},
        flag_threshold=decision.get("flag_threshold", 0.5),
        sensitive_threshold=decision.get("sensitive_threshold", 0.8),
        long_context_tokens=decision.get("long_context_tokens", 200_000),
        state_prompt_chars=decision.get("state_prompt_chars", 4000),
        blocked_data_classes=frozenset(decision.get("blocked_data_classes") or ()),
        num_retries=execution.get("num_retries", 1),
        timeout_s=execution.get("timeout_s", 120),
        max_output_tokens_default=execution.get("max_output_tokens_default", 4096),
        projects=projects,
    )
    if s.default_tier not in s.tiers:
        raise ValueError(f"default_tier {s.default_tier!r} is not a configured tier")
    return s
