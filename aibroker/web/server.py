from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)
import threading
import webbrowser
from contextlib import asynccontextmanager
from importlib import metadata as importlib_metadata
from pathlib import Path

import aibroker
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from aibroker.config.loader import load_profile
from aibroker.runner.orchestrator import run_once
from aibroker.simulation.demo_trades import append_demo_trade_tick, run_trade_demo_session
from aibroker.simulation.paper_autopilot import (
    configure_paper_autopilot,
    paper_fast_forward,
    paper_start,
    paper_status,
    paper_stop,
    start_paper_worker_thread,
    stop_paper_worker_thread,
)
from aibroker.web.demo_data import build_demo_charts

_STATIC = Path(__file__).resolve().parent / "static"
_run_once_log_lock = threading.Lock()


class PaperStartBody(BaseModel):
    deposit_usd: float = Field(default=100_000.0, ge=100.0, le=1_000_000_000.0)
    interval_sec: float = Field(default=2.0, ge=1.0, le=3600.0)
    leverage: float = Field(default=2.0, ge=1.0, le=10.0)
    start_date: str | None = Field(default=None)


class GrokChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class PlanBQuickBacktestBody(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY"])
    bars: int = Field(default=400, ge=60, le=1500)


class PlanBBacktestRunBody(BaseModel):
    symbol: str = Field(default="SPY")
    bars: int = Field(default=400, ge=60, le=1500)
    strategy_id: str = Field(default="ma_cross")
    strategy_params: dict = Field(default_factory=dict)
    initial_cash_usd: float = Field(default=100_000.0, ge=1000.0, le=1_000_000_000.0)


class PlanBSimStartBody(BaseModel):
    symbol: str = Field(default="SPY")
    bars: int = Field(default=400, ge=60, le=1500)
    strategy_id: str = Field(default="ma_cross")
    strategy_params: dict = Field(default_factory=dict)
    initial_cash_usd: float = Field(default=100_000.0, ge=1000.0, le=1_000_000_000.0)
    bar_source: str = Field(default="daily", pattern="^(daily|intraday)$")
    timeframe_minutes: int = Field(default=60, ge=1, le=240)


class PlanBSimStepBody(BaseModel):
    use_llm: bool = False


class PlanBPaperPlaceBody(BaseModel):
    symbol: str
    side: str = Field(pattern="^(buy|sell)$")
    quantity: float = Field(gt=0, le=1_000_000.0)


class PlanBKillSwitchBody(BaseModel):
    active: bool


class AgentStartBody(BaseModel):
    mode: str = Field(default="sim", pattern="^(sim|paper|live)$")
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "AMD"], min_length=1)
    deposit: float = Field(default=100_000.0, ge=100.0, le=1_000_000_000.0)
    start_date: str | None = Field(default=None)
    risk_level: str = Field(default="medium", pattern="^(low|medium|high)$")


class ListLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass


def create_app(profile_path: Path, *, port: int, open_browser: bool) -> FastAPI:
    profile_path = profile_path.resolve()

    # מצב הסוכן נשמר במשתני closure של אפליקציה זו בלבד. מנוע אחד לתהליך (למשל uvicorn עם worker יחיד)
    # מתאים לשימוש נוכחי; הרצת כמה workers תדרוש אחסון מצב חיצוני או sticky sessions.
    _agent_session = None
    _guardian = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal _agent_session, _guardian
        configure_paper_autopilot(profile_path)
        start_paper_worker_thread()
        try:
            cfg_init = load_profile(profile_path)
            from aibroker.data.alpha_vantage import ensure_bulk_loaded
            ensure_bulk_loaded(list(cfg_init.risk.allowed_symbols or []))
        except Exception:
            pass

        try:
            from aibroker.agent.persistence import restore_session
            restored = restore_session()
            if restored:
                _agent_session = restored
                log.info("Agent session auto-resumed from previous run")
        except Exception as e:
            log.warning("Failed to restore agent session: %s", e)

        from aibroker.agent.guardian import Guardian
        _guardian = Guardian(
            get_session=lambda: _agent_session,
            stop_session=lambda: _agent_session.stop() if _agent_session else None,
        )
        _guardian.start()

        if open_browser:

            def _open() -> None:
                webbrowser.open(f"http://127.0.0.1:{port}/")

            threading.Timer(0.8, _open).start()
        yield
        if _guardian:
            _guardian.stop()
        stop_paper_worker_thread()

    app = FastAPI(title="AI Broker", lifespan=lifespan)

    def _cfg():
        try:
            return load_profile(profile_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/")
    async def index() -> FileResponse:
        html = _STATIC / "index.html"
        if not html.is_file():
            raise HTTPException(status_code=500, detail="Missing static/index.html")
        return FileResponse(
            html,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/sim")
    async def sim_redirect() -> RedirectResponse:
        return RedirectResponse(url="/#simulation", status_code=302)

    @app.get("/planb")
    async def planb_redirect() -> RedirectResponse:
        return RedirectResponse(url="/#planb", status_code=302)

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/build-info")
    async def build_info() -> JSONResponse:
        """אבחון: וודא שהדפדפן מדבר עם אותו שרת FastAPI שמכיל את נתיבי הדמו."""
        try:
            ver = importlib_metadata.version("aibroker")
        except importlib_metadata.PackageNotFoundError:
            ver = "unknown"
        return JSONResponse(
            {
                "package_version": ver,
                "server_py": str(Path(__file__).resolve()),
                "aibroker_package_py": str(Path(aibroker.__file__).resolve()),
                "trade_demo_supported": True,
                "paper_autopilot_supported": True,
                "trade_demo_try_urls": [
                    "/api/simulation/trade-demo",
                    "/api/trade-demo",
                ],
                "paper_try_urls": [
                    "/api/paper/status",
                    "/api/simulation/paper/status",
                    "/api/paper_status",
                    "/api/paper/start",
                    "/api/simulation/paper/start",
                    "/api/paper/stop",
                    "/api/simulation/paper/stop",
                ],
                "planb_try_urls": [
                    "/api/planb/status",
                    "GET /api/planb/backtest/quick",
                    "/api/planb/config",
                    "/api/planb/data/intraday",
                    "/api/planb/backtest/quick",
                    "/api/planb/backtest/run",
                    "/api/planb/sim/start",
                    "/api/planb/sim/step",
                    "/api/planb/sim/status",
                    "/api/planb/sim/stop",
                    "/api/planb/paper/status",
                    "/api/planb/paper/place",
                    "/api/planb/risk/kill-switch",
                    "/api/planb/live/status",
                ],
            }
        )

    @app.get("/api/status")
    async def status() -> JSONResponse:
        cfg = _cfg()
        from aibroker.brokers.alpaca import alpaca_keys_set

        return JSONResponse(
            {
                "profile_name": cfg.profile_name,
                "broker": cfg.broker,
                "account_mode": cfg.account_mode,
                "dry_run": cfg.execution.dry_run,
                "grok_enabled": cfg.grok.enabled,
                "grok_role": cfg.grok.role,
                "allowed_symbols": cfg.risk.allowed_symbols,
                "alpha_vantage_key_set": bool(os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()),
                "alpaca_keys_set": alpaca_keys_set(),
            }
        )

    @app.get("/api/config")
    async def full_config() -> JSONResponse:
        cfg = _cfg()
        return JSONResponse(cfg.model_dump())

    @app.get("/api/demo-charts")
    async def demo_charts() -> JSONResponse:
        cfg = _cfg()
        return JSONResponse(build_demo_charts(cfg))

    @app.get("/api/modules")
    async def modules() -> JSONResponse:
        return JSONResponse(
            {
                "modules": [
                    {"id": "config", "name": "קונפיגורציה", "status": "ready", "note": "YAML + Pydantic"},
                    {"id": "data", "name": "נתוני שוק", "status": "ready", "note": "Alpha Vantage OHLC + Alpaca live quotes"},
                    {"id": "news", "name": "חדשות", "status": "ready", "note": "RSS + Grok Sentiment"},
                    {"id": "strategies", "name": "אסטרטגיות", "status": "ready", "note": "SMA Crossover · Momentum · Mean Reversion · Scalper"},
                    {"id": "risk", "name": "ריסק", "status": "ready", "note": "שער לפני ביצוע"},
                    {"id": "brokers", "name": "ברוקרים", "status": "ready", "note": "Alpaca Paper (מלא) + IBKR (חיבור)"},
                    {"id": "runner", "name": "Runner", "status": "ready", "note": "צעד יבש"},
                    {"id": "llm", "name": "Grok / LLM", "status": "partial", "note": "לקוח API + צ'אט CLI"},
                    {"id": "web", "name": "לוח בקרה", "status": "ready", "note": "ממשק דפדפן מקומי + נייר אוטונומי"},
                ]
            }
        )

    def _plan_b_cfg():
        from aibroker.planb.config import load_plan_b_config, plan_b_config_to_public_dict

        cfg = load_plan_b_config(main_profile=profile_path)
        return cfg, plan_b_config_to_public_dict(cfg)

    @app.get("/api/planb/status")
    async def planb_status() -> JSONResponse:
        _, pub = _plan_b_cfg()
        from aibroker.planb.risk_state import runtime_kill_switch_active

        return JSONResponse(
            {
                "ok": True,
                "track": "plan_b",
                "market": "US",
                "config": pub,
                "runtime_kill_switch": runtime_kill_switch_active(),
                "modes": {
                    "quick_backtest": "ready",
                    "full_backtest": "ready",
                    "step_sim": "ready",
                    "alpaca_paper": "ready",
                    "intraday_data": "placeholder",
                },
                "note_he": "מסלול נפרד מתוכנית א׳ — אסטרטגיה ומדדים כאן בגוף קוד שונה.",
            }
        )

    @app.get("/api/planb/config")
    async def planb_config_api() -> JSONResponse:
        _, pub = _plan_b_cfg()
        return JSONResponse({"ok": True, **pub})

    @app.get("/api/planb/data/intraday")
    async def planb_intraday_bars(
        symbols: str = "SPY",
        tf_min: int = 60,
        limit: int = 200,
    ) -> JSONResponse:
        """Optional Alpaca intraday bars for Plan B (empty lists without keys or on failure)."""
        from aibroker.planb.data.us_bars import load_us_intraday

        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:10]
        if not syms:
            syms = ["SPY"]
        lim = max(10, min(1000, int(limit)))
        tf = max(1, min(240, int(tf_min)))
        data = load_us_intraday(syms, timeframe_minutes=tf, limit=lim, prefer_alpaca=True)
        return JSONResponse({"ok": True, "symbols": syms, "timeframe_minutes": tf, "bars": data})

    @app.get("/api/planb/backtest/quick")
    async def planb_quick_backtest_get() -> JSONResponse:
        """Sanity check in browser: if this returns JSON, Plan B routes are loaded."""
        return JSONResponse(
            {
                "ok": True,
                "alive": True,
                "message_he": "השרת מכיר את Plan B. להרצת בדיקה אמיתית לחץ «הרץ בדיקה» בממשק (POST).",
                "post_json_example": {"symbols": ["SPY"], "bars": 400},
            }
        )

    @app.post("/api/planb/backtest/quick")
    async def planb_quick_backtest(body: PlanBQuickBacktestBody) -> JSONResponse:
        from aibroker.planb.quick_us_backtest import run_quick_us_momentum_backtest

        try:
            return JSONResponse(
                run_quick_us_momentum_backtest(body.symbols, body.bars, main_profile=profile_path)
            )
        except Exception as exc:
            log.exception("Plan B quick backtest failed")
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/api/planb/backtest/run")
    async def planb_backtest_run(body: PlanBBacktestRunBody) -> JSONResponse:
        from aibroker.planb.backtest.engine import run_backtest
        from aibroker.planb.data.us_bars import load_us_daily_bars
        from aibroker.planb.results import backtest_result_to_dict
        from aibroker.planb.risk_state import runtime_kill_switch_active
        from aibroker.planb.strategies.registry import build_strategy

        cfg, _ = _plan_b_cfg()
        sym = body.symbol.strip().upper()
        if sym not in cfg.risk.allowed_symbols:
            return JSONResponse({"ok": False, "error": f"symbol_not_allowed:{sym}"})
        try:
            strat = build_strategy(body.strategy_id, body.strategy_params)  # type: ignore[arg-type]
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)})
        hist = load_us_daily_bars([sym], bars=body.bars)
        bars_list = hist.get(sym) or []
        res = run_backtest(
            bars_list,
            strat,
            symbol=sym,
            initial_cash_usd=body.initial_cash_usd,
            costs=cfg.costs,
            risk=cfg.risk,
            oos=cfg.oos,
            kill_switch_active=runtime_kill_switch_active(),
        )
        return JSONResponse(backtest_result_to_dict(res, bars=bars_list if res.ok else None))

    @app.post("/api/planb/sim/start")
    async def planb_sim_start_api(body: PlanBSimStartBody) -> JSONResponse:
        from aibroker.planb.sim.session import planb_sim_start

        cfg, _ = _plan_b_cfg()
        out = planb_sim_start(
            cfg,
            symbol=body.symbol,
            bars=body.bars,
            strategy_id=body.strategy_id,
            strategy_params=body.strategy_params,
            initial_cash=body.initial_cash_usd,
            bar_source=body.bar_source,
            timeframe_minutes=body.timeframe_minutes,
        )
        return JSONResponse(out)

    @app.post("/api/planb/sim/step")
    async def planb_sim_step_api(body: PlanBSimStepBody) -> JSONResponse:
        from aibroker.planb.sim.session import planb_sim_step

        return JSONResponse(planb_sim_step(use_llm=body.use_llm))

    @app.get("/api/planb/sim/status")
    async def planb_sim_status_api() -> JSONResponse:
        from aibroker.planb.sim.session import planb_sim_status

        return JSONResponse(planb_sim_status())

    @app.post("/api/planb/sim/stop")
    async def planb_sim_stop_api() -> JSONResponse:
        from aibroker.planb.sim.session import planb_sim_stop

        return JSONResponse(planb_sim_stop())

    @app.get("/api/planb/paper/status")
    async def planb_paper_status_api() -> JSONResponse:
        from aibroker.planb.execution.paper_runner import planb_paper_status

        return JSONResponse(planb_paper_status())

    @app.post("/api/planb/paper/place")
    async def planb_paper_place_api(body: PlanBPaperPlaceBody) -> JSONResponse:
        from aibroker.planb.execution.paper_runner import planb_paper_place

        cfg, _ = _plan_b_cfg()
        return JSONResponse(
            planb_paper_place(
                cfg,
                symbol=body.symbol,
                side=body.side,  # type: ignore[arg-type]
                quantity=body.quantity,
            )
        )

    @app.post("/api/planb/risk/kill-switch")
    async def planb_kill_switch_api(body: PlanBKillSwitchBody) -> JSONResponse:
        from aibroker.planb.risk_state import set_runtime_kill_switch

        set_runtime_kill_switch(body.active)
        return JSONResponse({"ok": True, "runtime_kill_switch": body.active})

    @app.get("/api/planb/live/status")
    async def planb_live_status_api() -> JSONResponse:
        from aibroker.planb.live.guards import live_execution_allowed

        cfg, _ = _plan_b_cfg()
        main = _cfg()
        return JSONResponse(
            live_execution_allowed(
                plan_b_live_enabled=cfg.live.enabled,
                account_mode_live=main.account_mode == "live",
            )
        )

    @app.post("/api/run-once")
    async def run_once_api() -> JSONResponse:
        cfg = _cfg()
        with _run_once_log_lock:
            lg = logging.getLogger("aibroker")
            h = ListLogHandler()
            h.setLevel(logging.INFO)
            lg.addHandler(h)
            prev_level = lg.level
            lg.setLevel(logging.INFO)
            try:
                run_once(cfg, connect_broker=False)
                text = "\n".join(h.lines) if h.lines else "הסתיים (אין שורות לוג — בדוק שרמת לוג)."
                return JSONResponse({"lines": text})
            finally:
                lg.removeHandler(h)
                lg.setLevel(prev_level)

    def _trade_demo_payload() -> JSONResponse:
        cfg = _cfg()
        return JSONResponse(run_trade_demo_session(cfg))

    @app.post("/api/simulation/trade-demo")
    async def trade_demo() -> JSONResponse:
        """דמו קניות/מכירות: שער ריסק אמיתי, ביצוע מדומה בלבד (dry_run)."""
        return _trade_demo_payload()

    # גיבוי קצר — אם משתמש עם חבילה ישנה / פרוקסי, לפעמים קל יותר לאבחן
    @app.post("/api/trade-demo")
    async def trade_demo_alias() -> JSONResponse:
        return _trade_demo_payload()

    @app.get("/api/simulation/trade-demo")
    async def trade_demo_get() -> JSONResponse:
        """אותו JSON כמו POST — נוח לבדיקה בכתובת בדפדפן וכגיבוי אם POST נחסם."""
        return _trade_demo_payload()

    @app.get("/api/trade-demo")
    async def trade_demo_get_alias() -> JSONResponse:
        return _trade_demo_payload()

    def _trade_tick_payload() -> JSONResponse:
        cfg = _cfg()
        return JSONResponse(append_demo_trade_tick(cfg))

    @app.post("/api/simulation/trade-tick")
    async def trade_tick() -> JSONResponse:
        """עסקת דמו נוספת — מצטבר לרשימה (זרם חי מקומי, דמו בלבד)."""
        return _trade_tick_payload()

    @app.post("/api/trade-tick")
    async def trade_tick_alias() -> JSONResponse:
        return _trade_tick_payload()

    @app.get("/api/simulation/trade-tick")
    async def trade_tick_get() -> JSONResponse:
        return _trade_tick_payload()

    @app.get("/api/trade-tick")
    async def trade_tick_get_alias() -> JSONResponse:
        return _trade_tick_payload()

    @app.get("/api/paper/status")
    @app.get("/api/simulation/paper/status")
    @app.get("/api/paper_status")
    async def paper_status_api() -> JSONResponse:
        cfg = _cfg()
        return JSONResponse(paper_status(cfg))

    @app.post("/api/paper/start")
    @app.post("/api/simulation/paper/start")
    async def paper_start_api(payload: PaperStartBody) -> JSONResponse:
        cfg = _cfg()
        out = paper_start(
            cfg,
            deposit_usd=payload.deposit_usd,
            interval_sec=payload.interval_sec,
            leverage=payload.leverage,
            start_date=payload.start_date,
        )
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("error", "start failed"))
        return JSONResponse(out)

    @app.post("/api/paper/stop")
    @app.post("/api/simulation/paper/stop")
    async def paper_stop_api() -> JSONResponse:
        paper_stop()
        cfg = _cfg()
        return JSONResponse(paper_status(cfg))

    @app.post("/api/paper/fast-forward")
    async def paper_fast_forward_api(payload: PaperStartBody = PaperStartBody()) -> JSONResponse:
        cfg = _cfg()
        from aibroker.simulation.paper_autopilot import _session
        if _session is None or not _session.running:
            start_out = paper_start(
                cfg,
                deposit_usd=payload.deposit_usd,
                interval_sec=payload.interval_sec,
                leverage=payload.leverage,
                start_date=payload.start_date,
            )
            if not start_out.get("ok"):
                raise HTTPException(status_code=400, detail=start_out.get("error", "start failed"))
        out = paper_fast_forward(cfg)
        return JSONResponse(out)

    @app.get("/api/news/sentiment")
    async def news_sentiment_api() -> JSONResponse:
        cfg = _cfg()
        symbols = list(cfg.risk.allowed_symbols or ["SPY", "QQQ"])
        try:
            from aibroker.news.ingest import fetch_sentiment_for_symbols
            scores = fetch_sentiment_for_symbols(symbols)
            return JSONResponse({"ok": True, "sentiment": scores, "symbols": symbols})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc), "sentiment": {}})

    @app.get("/api/news/details")
    async def news_details_api() -> JSONResponse:
        """Returns all RSS headlines + per-symbol filtering + Grok sentiment with Hebrew analysis."""
        cfg = _cfg()
        symbols = list(cfg.risk.allowed_symbols or ["SPY", "QQQ"])
        try:
            from aibroker.news.rss_fetcher import fetch_all_headlines, filter_headlines_for_symbol
            all_h = fetch_all_headlines()
            per_symbol_headlines: dict[str, list] = {}
            for sym in symbols:
                per_symbol_headlines[sym] = filter_headlines_for_symbol(all_h, sym, max_results=10)

            grok_key = bool(os.environ.get("GROK_API_KEY", "").strip())
            detailed: dict[str, dict] = {}
            if grok_key:
                try:
                    from aibroker.news.sentiment import score_all_symbols_detailed
                    detailed = score_all_symbols_detailed(symbols, per_symbol_headlines)
                except Exception as e:
                    log.warning("Sentiment scoring failed: %s", e)

            per_symbol_out = {}
            for sym in symbols:
                h = per_symbol_headlines[sym]
                d = detailed.get(sym, {})
                per_symbol_out[sym] = {
                    "headlines": h,
                    "sentiment": float(d.get("sentiment", 0.0)),
                    "confidence": float(d.get("confidence", 0.0)),
                    "summary_he": d.get("summary_he", d.get("summary", "")),
                    "reasoning_he": d.get("reasoning_he", ""),
                    "factors": d.get("factors", []),
                    "headlines_he": d.get("headlines_he", []),
                    "headlines_used": int(d.get("headlines_used", 0)),
                }

            return JSONResponse({
                "ok": True,
                "total_headlines": len(all_h),
                "all_headlines": all_h[:50],
                "per_symbol": per_symbol_out,
                "grok_enabled": grok_key,
            })
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})

    # ── Grok real-time chat ──

    _grok_history: list[dict[str, str]] = []

    @app.post("/api/grok/chat")
    async def grok_chat_api(body: GrokChatBody) -> JSONResponse:
        grok_key = os.environ.get("GROK_API_KEY", "").strip()
        if not grok_key:
            return JSONResponse({"ok": False, "error": "חסר GROK_API_KEY — הגדר ב-.env"})

        cfg = _cfg()
        symbols = list(cfg.risk.allowed_symbols or [])

        context_parts = [f"סימבולים במעקב: {', '.join(symbols)}"]
        from aibroker.simulation.paper_autopilot import _session, _latest_sentiment
        s = _session
        if s is not None and s.running:
            from aibroker.simulation.paper_autopilot import _mark_prices, _equity
            mark = _mark_prices(s)
            eq = _equity(s, mark)
            pnl = eq - s.initial_deposit_usd
            pnl_pct = pnl / s.initial_deposit_usd * 100 if s.initial_deposit_usd > 0 else 0
            context_parts.append(f"סימולציה פעילה: יום {s.current_date}, הון ${eq:,.0f}, רווח {pnl_pct:+.1f}%")
            if s.positions:
                pos_lines = []
                for sym, p in s.positions.items():
                    q = float(p.get("qty", 0))
                    if abs(q) > 0.01:
                        side = "LONG" if q > 0 else "SHORT"
                        pos_lines.append(f"  {sym}: {side} {abs(q):.0f} @ ${float(p.get('avg_px', 0)):,.2f}")
                if pos_lines:
                    context_parts.append("פוזיציות פתוחות:\n" + "\n".join(pos_lines))
            if s.closed_trades:
                context_parts.append(f"עסקאות סגורות: {len(s.closed_trades)}, אחוז ניצחון: {s.win_rate}%, R ממוצע: {s.avg_r}")
        if _latest_sentiment:
            sent_lines = [f"  {sym}: {sc:+.2f}" for sym, sc in sorted(_latest_sentiment.items()) if abs(sc) > 0.05]
            if sent_lines:
                context_parts.append("סנטימנט חדשות:\n" + "\n".join(sent_lines))

        portfolio_context = "\n".join(context_parts)

        system_prompt = f"""\
אתה Grok — יועץ פיננסי AI חכם שעובד בתוך מערכת AI Broker.
אתה עונה תמיד בעברית.
יש לך גישה למידע הבא על הפורטפוליו הנוכחי:
{portfolio_context}

כללים:
- ענה בעברית בלבד, בצורה ברורה ומקצועית
- תן ניתוחים מבוססים על עובדות
- הזהר תמיד שזו סימולציה ולא ייעוץ פיננסי אמיתי
- אם נשאל על מניה ספציפית, תן ניתוח טכני ופונדמנטלי קצר
- היה תמציתי אבל מקיף"""

        _grok_history.append({"role": "user", "content": body.message})
        if len(_grok_history) > 20:
            _grok_history[:] = _grok_history[-20:]

        try:
            from aibroker.llm.grok import get_chat_client
            import httpx

            client = get_chat_client()
            payload = {
                "model": client.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *_grok_history,
                ],
                "temperature": client.temperature,
                "max_tokens": client.max_tokens,
            }
            async with httpx.AsyncClient(timeout=client.timeout_s) as http:
                r = await http.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers=client._headers(),
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                client._track_usage(data)
            reply = data["choices"][0]["message"]["content"]
            _grok_history.append({"role": "assistant", "content": reply})

            return JSONResponse({"ok": True, "reply": reply})
        except Exception as exc:
            log.warning("Grok chat failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/api/grok/clear")
    async def grok_clear_api() -> JSONResponse:
        _grok_history.clear()
        return JSONResponse({"ok": True})

    @app.get("/api/grok/usage")
    async def grok_usage_api() -> JSONResponse:
        from aibroker.llm.grok import usage
        return JSONResponse({"ok": True, **usage.summary()})

    # ── Step-once for real-time simulation ──

    @app.post("/api/paper/step")
    async def paper_step_api() -> JSONResponse:
        cfg = _cfg()
        from aibroker.simulation.paper_autopilot import _session, _step_once, _lock

        def _locked_step() -> tuple[bool, str | None]:
            with _lock:
                s = _session
                if s is None:
                    return False, "אין סימולציה פעילה"
                if not s.running:
                    return False, "הסימולציה הסתיימה"
                try:
                    _step_once(cfg, s)
                except Exception as e:
                    return False, str(e)
            return True, None

        ok, err = await asyncio.to_thread(_locked_step)
        if not ok:
            return JSONResponse({"ok": False, "error": err})
        return JSONResponse({"ok": True, **paper_status(cfg)})

    @app.get("/api/alpaca/account")
    async def alpaca_account() -> JSONResponse:
        from aibroker.brokers.alpaca import AlpacaBrokerClient, alpaca_keys_set

        if not alpaca_keys_set():
            return JSONResponse(
                {"ok": False, "error": "ALPACA_API_KEY / ALPACA_SECRET_KEY לא מוגדרים ב-.env"},
                status_code=200,
            )
        cfg = _cfg()
        client = AlpacaBrokerClient(paper=cfg.account_mode == "paper")
        try:
            client.connect()
            acct = client.get_account()
            return JSONResponse({"ok": True, **acct})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})
        finally:
            client.disconnect()

    @app.get("/api/alpaca/positions")
    async def alpaca_positions() -> JSONResponse:
        from aibroker.brokers.alpaca import AlpacaBrokerClient, alpaca_keys_set

        if not alpaca_keys_set():
            return JSONResponse(
                {"ok": False, "error": "ALPACA_API_KEY / ALPACA_SECRET_KEY לא מוגדרים ב-.env"},
                status_code=200,
            )
        cfg = _cfg()
        client = AlpacaBrokerClient(paper=cfg.account_mode == "paper")
        try:
            client.connect()
            pos = client.positions()
            return JSONResponse({"ok": True, "positions": pos})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})
        finally:
            client.disconnect()

    @app.get("/api/alpaca/quotes")
    async def alpaca_quotes() -> JSONResponse:
        from aibroker.brokers.alpaca import alpaca_keys_set, fetch_alpaca_quote_book

        if not alpaca_keys_set():
            return JSONResponse({"ok": False, "error": "מפתחות Alpaca לא מוגדרים"})
        cfg = _cfg()
        symbols = cfg.risk.allowed_symbols or ["SPY", "QQQ"]
        book = fetch_alpaca_quote_book(symbols)
        prices = {k: v["mid"] for k, v in book.items()}
        return JSONResponse({
            "ok": True,
            "prices": prices,
            "book": book,
            "source": "alpaca_live",
            "note": "mid=ממוצע; לשמר על ריאליזם: קנייה קרוב ל-ask, מכירה ל-bid",
        })

    # ══════════════════════════════════════════════════
    # AI Agent routes
    # ══════════════════════════════════════════════════

    @app.post("/api/agent/start")
    async def agent_start_api(body: AgentStartBody) -> JSONResponse:
        nonlocal _agent_session
        from aibroker.agent.loop import AgentSession

        if _agent_session is not None:
            try:
                await asyncio.to_thread(_agent_session.stop)
            except Exception:
                _agent_session.running = False
        session = AgentSession(
            mode=body.mode,
            symbols=body.symbols,
            deposit=body.deposit,
            start_date=body.start_date,
            risk_level=body.risk_level,
        )
        _agent_session = session
        out = await asyncio.to_thread(session.start)
        try:
            from aibroker.agent.alerts import alert_agent_started
            alert_agent_started(body.mode, body.risk_level, len(body.symbols), body.deposit)
        except Exception:
            pass
        return JSONResponse({"ok": True, **out})

    @app.post("/api/agent/tick")
    async def agent_tick_api() -> JSONResponse:
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": False, "error": "no_session"})
        try:
            out = await asyncio.to_thread(session.tick)
            return JSONResponse({"ok": True, **out})
        except Exception as e:
            log.exception("Agent tick error")
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/agent/tick-fast")
    async def agent_tick_fast_api() -> JSONResponse:
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": False, "error": "no_session"})
        try:
            out = await asyncio.to_thread(session.tick_fast)
            return JSONResponse({"ok": True, **out})
        except Exception as e:
            log.exception("Agent fast tick error")
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/agent/status")
    async def agent_status_api() -> JSONResponse:
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": False, "running": False, "error": "no_session"})
        return JSONResponse({"ok": True, **session.status()})

    @app.post("/api/agent/stop")
    async def agent_stop_api() -> JSONResponse:
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": True, "running": False})
        out = await asyncio.to_thread(session.stop)
        try:
            from aibroker.agent.alerts import alert_agent_stopped
            alert_agent_stopped(out.get("equity", 0), out.get("pnl", 0), out.get("pnl_pct", 0), "ידני")
        except Exception:
            pass
        return JSONResponse({"ok": True, **out})

    @app.get("/api/agent/trades")
    async def agent_trades_api() -> JSONResponse:
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": False, "trades": []})
        return JSONResponse({"ok": True, "trades": session.trades})

    @app.get("/api/agent/decisions")
    async def agent_decisions_api() -> JSONResponse:
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": False, "decisions": []})
        return JSONResponse({"ok": True, "decisions": session.decisions[-20:]})

    @app.get("/api/agent/live-prices")
    async def agent_live_prices_api() -> JSONResponse:
        """Fetch real-time prices from Alpaca and return updated portfolio."""
        session = _agent_session
        if session is None:
            return JSONResponse({"ok": False})
        if session.mode not in ("paper", "live"):
            return JSONResponse({"ok": False, "reason": "sim_mode"})

        def _fetch_alpaca_portfolio(sess: object) -> dict[str, object]:
            from aibroker.brokers.alpaca import AlpacaBrokerClient

            broker = AlpacaBrokerClient(paper=(sess.mode == "paper"))
            broker.connect()
            try:
                acct = broker.get_account()
                positions = broker.positions()
            finally:
                broker.disconnect()

            equity = float(acct.get("equity_usd", 0))
            cash = float(acct.get("cash_usd", 0))
            pnl_pct = (equity / sess.initial_deposit - 1) * 100 if sess.initial_deposit > 0 else 0

            pos_detail = []
            for p in positions:
                qty = float(p.get("qty", 0))
                avg = float(p.get("avg_cost", 0))
                cur = float(p.get("current_price", 0))
                side = "long" if qty > 0 else "short"
                upl = float(p.get("unrealized_pl", 0))
                upl_pct = ((cur / avg - 1) * 100 * (1 if qty > 0 else -1)) if avg > 0 else 0
                pos_detail.append({
                    "symbol": p["symbol"],
                    "side": side,
                    "qty": abs(qty),
                    "avg_cost": round(avg, 2),
                    "current_price": round(cur, 2),
                    "market_value": round(abs(qty) * cur, 2),
                    "unrealized_pnl": round(upl, 2),
                    "unrealized_pnl_pct": round(upl_pct, 2),
                })

            return {
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "pnl": round(equity - sess.initial_deposit, 2),
                "pnl_pct": round(pnl_pct, 2),
                "positions_detail": pos_detail,
            }

        try:
            data = await asyncio.to_thread(_fetch_alpaca_portfolio, session)
            return JSONResponse({"ok": True, **data})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/agent/history")
    async def agent_history_api() -> JSONResponse:
        from aibroker.data.storage import get_session_history
        try:
            sessions = get_session_history(limit=30)
            return JSONResponse({"ok": True, "sessions": sessions})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e), "sessions": []})

    @app.get("/api/agent/history/{session_id}/trades")
    async def agent_history_trades_api(session_id: int) -> JSONResponse:
        from aibroker.data.storage import get_session_trades
        try:
            trades = get_session_trades(session_id)
            return JSONResponse({"ok": True, "trades": trades})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e), "trades": []})

    return app
