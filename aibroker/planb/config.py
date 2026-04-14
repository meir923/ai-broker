"""Plan B configuration — separate from AppConfig (Plan A).

Boundaries:
- Plan A modules must not import from `aibroker.planb` except via explicit user choice.
- Plan B may import `aibroker.brokers.alpaca` and `aibroker.data.historical` as shared infrastructure only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

class PlanBCostsConfig(BaseModel):
    """Per-share fee + optional pct of notional; slippage as fraction of price."""

    fee_per_share_usd: float = Field(default=0.005, ge=0.0)
    fee_pct_of_notional: float = Field(default=0.0005, ge=0.0, le=0.05)
    slippage_pct: float = Field(default=0.0005, ge=0.0, le=0.05)


class PlanBRiskConfig(BaseModel):
    max_notional_per_trade_usd: float = Field(default=25_000.0, gt=0)
    max_trades_per_day: int = Field(default=50, ge=0)
    max_daily_loss_usd: float = Field(default=5_000.0, gt=0)
    kill_switch: bool = False
    allowed_symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ"])

    @field_validator("allowed_symbols", mode="before")
    @classmethod
    def upper_syms(cls, v: list[str]) -> list[str]:
        if not v:
            return []
        return [str(s).strip().upper() for s in v]


class PlanBOOSConfig(BaseModel):
    """Walk-forward / hold-out reporting."""

    mode: Literal["holdout_end", "none"] = "holdout_end"
    train_fraction: float = Field(default=0.7, gt=0.1, lt=0.95)


class PlanBSuccessCriteriaConfig(BaseModel):
    """Documentation targets for backtests (not enforced automatically)."""

    min_sharpe_annualized: float | None = None
    max_drawdown_pct: float | None = None
    min_trades_for_significance: int | None = Field(default=30, ge=1)


class PlanBDataConfig(BaseModel):
    default_bars: int = Field(default=400, ge=60, le=2000)
    intraday_enabled: bool = False


class PlanBLLMConfig(BaseModel):
    enabled: bool = False
    """When true, optional Grok JSON advisor runs inside risk gates (sim / paper hooks)."""
    max_position_pct_equity: float = Field(default=0.25, gt=0, le=1.0)


class PlanBLiveConfig(BaseModel):
    """Live trading is opt-in and gated by env (mirrors AppConfig live gate)."""

    enabled: bool = False


class PlanBConfig(BaseModel):
    profile_name: str = "plan_b_us"
    market: Literal["US"] = "US"
    data: PlanBDataConfig = Field(default_factory=PlanBDataConfig)
    costs: PlanBCostsConfig = Field(default_factory=PlanBCostsConfig)
    risk: PlanBRiskConfig = Field(default_factory=PlanBRiskConfig)
    oos: PlanBOOSConfig = Field(default_factory=PlanBOOSConfig)
    success_criteria: PlanBSuccessCriteriaConfig = Field(
        default_factory=PlanBSuccessCriteriaConfig
    )
    llm: PlanBLLMConfig = Field(default_factory=PlanBLLMConfig)
    live: PlanBLiveConfig = Field(default_factory=PlanBLiveConfig)


def default_plan_b_profile_path(main_profile: Path | None = None) -> Path:
    if main_profile is not None:
        p = main_profile.resolve().parent / "plan_b_us.yaml"
        if p.is_file():
            return p
    root = Path(__file__).resolve().parents[2]
    env = os.environ.get("PLAN_B_PROFILE", "").strip()
    if env:
        ep = Path(env)
        return ep if ep.is_absolute() else (root / ep).resolve()
    cand = root / "config" / "profiles" / "plan_b_us.yaml"
    return cand


def load_plan_b_config(path: str | Path | None = None, *, main_profile: Path | None = None) -> PlanBConfig:
    p = Path(path) if path else default_plan_b_profile_path(main_profile)
    if not p.is_file():
        return PlanBConfig()
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Plan B profile must be a mapping: {p}")
    return PlanBConfig.model_validate(raw)


def plan_b_config_to_public_dict(cfg: PlanBConfig) -> dict[str, Any]:
    """Safe subset for API / UI (no secrets)."""
    return {
        "profile_name": cfg.profile_name,
        "market": cfg.market,
        "allowed_symbols": cfg.risk.allowed_symbols,
        "costs": cfg.costs.model_dump(),
        "oos": cfg.oos.model_dump(),
        "success_criteria": cfg.success_criteria.model_dump(),
        "llm_enabled": cfg.llm.enabled,
        "live_enabled": cfg.live.enabled,
        "intraday_enabled": cfg.data.intraday_enabled,
    }
