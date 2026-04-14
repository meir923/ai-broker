from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BrokerName = Literal["ibkr", "alpaca"]
AccountMode = Literal["paper", "live"]
GrokRole = Literal["off", "news_only", "order_proposals"]
GrokApproval = Literal["manual", "auto_within_risk"]
StrategyMode = Literal["rules", "rules_with_news_filter", "merge_grok_proposals"]
ColmexSignals = Literal["off", "notify_only"]
NotifyChannel = Literal["none", "telegram", "email"]
ChatUi = Literal["cli", "web"]
ChatContextModule = Literal[
    "profile",
    "positions",
    "orders",
    "signals",
    "news_digest",
    "risk",
    "logs_tail",
]


class ExecutionConfig(BaseModel):
    dry_run: bool = True


class GrokOrdersConfig(BaseModel):
    approval: GrokApproval = "manual"


class GrokChatConfig(BaseModel):
    enabled: bool = False
    ui: ChatUi = "cli"
    context: list[ChatContextModule] = Field(default_factory=list)


class GrokConfig(BaseModel):
    enabled: bool = False
    role: GrokRole = "off"
    orders: GrokOrdersConfig = Field(default_factory=GrokOrdersConfig)
    chat: GrokChatConfig = Field(default_factory=GrokChatConfig)


class StrategyConfig(BaseModel):
    mode: StrategyMode = "rules"


class SignalsConfig(BaseModel):
    colmex: ColmexSignals = "off"


class NotificationsConfig(BaseModel):
    channel: NotifyChannel = "none"


class RiskConfig(BaseModel):
    max_daily_loss_usd: float = Field(gt=0)
    max_notional_per_trade_usd: float = Field(gt=0)
    max_trades_per_day: int = Field(ge=0)
    kill_switch: bool = False
    allowed_symbols: list[str] = Field(default_factory=list)

    @field_validator("allowed_symbols", mode="before")
    @classmethod
    def upper_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            return []
        return [s.strip().upper() for s in v]


class IbkrConnectionConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1


class AppConfig(BaseModel):
    profile_name: str = "default"
    broker: BrokerName = "ibkr"
    account_mode: AccountMode = "paper"
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    grok: GrokConfig = Field(default_factory=GrokConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    ibkr: IbkrConnectionConfig = Field(default_factory=IbkrConnectionConfig)

    @model_validator(mode="after")
    def colmex_consistency(self) -> AppConfig:
        if self.signals.colmex != "off" and self.broker != "ibkr":
            # Plan: Colmex has no auto execution until adapter exists; signals are notify-only.
            pass
        return self
