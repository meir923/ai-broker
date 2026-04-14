from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class RuntimeState(BaseModel):
    """Shared snapshot for runner + chat ContextSnapshot (no secrets)."""

    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    profile_name: str = ""
    account_mode: str = ""
    dry_run: bool = True
    kill_switch: bool = False
    daily_pnl_usd: float = 0.0
    equity_usd: float = 0.0
    trades_today: int = 0
    positions: list[dict[str, Any]] = Field(default_factory=list)
    open_orders: list[dict[str, Any]] = Field(default_factory=list)
    recent_signals: list[dict[str, Any]] = Field(default_factory=list)
    news_digest: list[dict[str, Any]] = Field(default_factory=list)
    recent_errors: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}
