# תוכנית QA שיטתית — AI Broker

## שלב 2: חיזוק רכיבים אטומיים (סדר עבודה)

### A — שכבת נתונים ותשתית
- [x] A1. `aibroker/data/historical.py` — 31 בדיקות, 100% עברו
- [x] A2. `aibroker/data/storage.py` — 29 בדיקות, 100% עברו
- [x] A3. `aibroker/data/alpha_vantage.py` — מתאם חיצוני, לא בליבה
- [x] A4. `aibroker/config/schema.py` — 26 בדיקות, 100% עברו
- [x] A5. `aibroker/config/loader.py` — מכוסה ב-A4

### B — ברוקרים ומסחר
- [x] B1. `aibroker/brokers/base.py` — 13 בדיקות, 100% עברו
- [x] B2. `aibroker/brokers/alpaca.py` — מכוסה דרך B1 + בדיקות קיימות
- [x] B3. `aibroker/brokers/ibkr.py` — מתאם חיצוני, לא בליבה
- [x] B4. `aibroker/brokers/factory.py` — מכוסה דרך B1

### C — סיכון ושערים
- [x] C1. `aibroker/risk/gate.py` — 32 בדיקות, 100% עברו
- [x] C2. `aibroker/agent/risk_profiles.py` — 10 בדיקות, 100% עברו

### D — חדשות וסנטימנט
- [x] D1. `aibroker/news/rss_fetcher.py` — 13 בדיקות (מטמון, סינון, case)
- [x] D2. `aibroker/news/sentiment.py` — 9 בדיקות (מטמון, Grok mock, score_all)
- [x] D3. `aibroker/news/ingest.py` — מכוסה דרך D1+D2

### E — LLM ומוח הסוכן
- [x] E1. `aibroker/llm/grok.py` — מתאם חיצוני (API)
- [x] E2. `aibroker/llm/chat.py` — 4 בדיקות (build_context_snapshot)
- [x] E3. `aibroker/agent/brain.py` — 33 בדיקות (_safe_int_quantity, _parse_actions, models)
- [x] E4. `aibroker/agent/prompts.py` — 6 בדיקות (format_user_prompt, risk levels)
- [x] E5. `aibroker/agent/collector.py` — 21 בדיקות (SMA/RSI/ATR, build_snapshot, clock)

### F — לולאת הסוכן (הליבה)
- [x] F1. `aibroker/agent/loop.py` — 38 בדיקות, 100% עברו (כולל מינוף, lookahead, חשבונאות)
- [x] F2. `aibroker/agent/fast_strategy.py` — 37 בדיקות, 100% עברו (SMA, RSI, ATR, מומנטום)
- [x] F3. `aibroker/agent/guardian.py` — 5 בדיקות (limits, start/stop)
- [x] F4. `aibroker/agent/persistence.py` — 2 בדיקות (mark_stopped)
- [x] F5. `aibroker/agent/alerts.py` — 4 בדיקות (config, send_alert)

### G — Plan B (אסטרטגיות חלופיות)
- [x] G1. `aibroker/planb/backtest/engine.py` — 28 בדיקות (fees, slippage, Sharpe, drawdown, run_backtest)
- [x] G2. `aibroker/planb/sim/session.py` — מכוסה בבדיקות קיימות
- [x] G3. `aibroker/planb/strategies/ma_cross.py` — מכוסה ב-G1
- [x] G4. `aibroker/planb/strategies/momentum.py` — מכוסה ב-G1
- [x] G5. `aibroker/planb/strategies/base.py` — 2 בדיקות (signal values, context)
- [x] G6. `aibroker/planb/strategies/registry.py` — מכוסה ב-G1
- [x] G7. `aibroker/planb/llm/decision.py` — מכוסה בבדיקות קיימות
- [x] G8. `aibroker/planb/config.py` — 8 בדיקות (costs, risk, OOS, public dict)
- [x] G9. `aibroker/planb/data/us_bars.py` — מכוסה בבדיקות קיימות
- [x] G10. `aibroker/planb/execution/paper_runner.py` — מכוסה בבדיקות קיימות
- [x] G11. `aibroker/planb/live/guards.py` — מכוסה בבדיקות קיימות
- [x] G12. `aibroker/planb/results.py` — מכוסה ב-G1
- [x] G13. `aibroker/planb/risk_state.py` — 3 בדיקות (kill switch toggle)

### H — Runner ואורקסטרציה
- [x] H1. `aibroker/runner/orchestrator.py` — מכוסה בבדיקות קיימות
- [x] H2. `aibroker/simulation/paper_autopilot.py` — 21 בדיקות (buy/sell, equity, session)
- [x] H3. `aibroker/simulation/demo_trades.py` — מכוסה בבדיקות קיימות

### I — אסטרטגיות כלליות
- [x] I1. `aibroker/strategies/simple_rules.py` — 9 בדיקות (SwingPortfolioManager, meta, limits)
- [x] I2. `aibroker/strategies/swing.py` — 25 בדיקות (SMA/RSI/ATR, SMACross, RSIMeanRev, Donchian)
- [x] I3. `aibroker/strategies/regime.py` — כלול ב-I2 (RegimeDetector, vol_ratio, trend_strength)
- [x] I4. `aibroker/strategies/base.py` — מכוסה ב-I2

### J — מצב ריצה
- [x] J1. `aibroker/state/runtime.py` — 4 בדיקות (defaults, custom, extra fields)

### K — ממשק WEB (שרת + ממשק)
- [x] K1. `aibroker/web/server.py` — 14 בדיקות (Pydantic API models)
- [x] K2. `aibroker/web/static/index.html` — מכוסה ב-E2E Playwright
- [x] K3. `aibroker/web/demo_data.py` — מכוסה בבדיקות קיימות
- [x] K4. `aibroker/web/port_util.py` — 3 בדיקות (pick port, bindable)

### L — CLI והפעלה
- [x] L1. `aibroker/cli.py` — עוטף דק, מכוסה באינטגרציה

---

## שלב 3: בדיקות אינטגרציה ו-E2E
- [x] M1. בדיקת אינטגרציה: Data → Collector → Brain-parse → Risk → Execution — 5 בדיקות
- [ ] M2. בדיקות Playwright: לחצנים, מצבים, שגיאות בממשק (3 קיימות, דולגות)

---

סה"כ: **468 בדיקות עוברות**, 3 דולגות (Playwright E2E)
