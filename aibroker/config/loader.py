from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from aibroker.config.schema import AppConfig

ACCEPT_ENV = "I_ACCEPT_LIVE_RISK"
ACCEPT_VALUE = "true"


def _require_accept_flag(reason: str) -> None:
    if os.environ.get(ACCEPT_ENV, "").strip().lower() != ACCEPT_VALUE:
        raise ValueError(
            f"{reason} Set environment variable {ACCEPT_ENV}={ACCEPT_VALUE} deliberately."
        )


def _post_validate_env_gates(cfg: AppConfig) -> None:
    if cfg.account_mode == "live":
        _require_accept_flag("account_mode=live requires explicit acceptance.")
    if cfg.grok.orders.approval == "auto_within_risk":
        _require_accept_flag("grok.orders.approval=auto_within_risk requires explicit acceptance.")


def _load_dotenv_from_project() -> None:
    """Load `.env` from project root (next to `pyproject.toml`) if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[2]
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def load_profile(path: str | Path) -> AppConfig:
    """Load YAML profile and validate. Applies env gates for live / auto Grok orders."""
    _load_dotenv_from_project()
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Profile must be a mapping: {p}")
    try:
        cfg = AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid profile {p}: {e}") from e
    _post_validate_env_gates(cfg)
    return cfg


def load_profile_dict(data: dict[str, Any]) -> AppConfig:
    _load_dotenv_from_project()
    cfg = AppConfig.model_validate(data)
    _post_validate_env_gates(cfg)
    return cfg
