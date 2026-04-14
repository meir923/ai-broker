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
- [ ] D1. `aibroker/news/rss_fetcher.py` — שליפת RSS עם הגנת XML
- [ ] D2. `aibroker/news/sentiment.py` — ניקוד סנטימנט לפי סמל (Grok)
- [ ] D3. `aibroker/news/ingest.py` — צינור עיבוד חדשות

### E — LLM ומוח הסוכן
- [ ] E1. `aibroker/llm/grok.py` — קריאה ל-Grok API, ניתוח JSON
- [ ] E2. `aibroker/llm/chat.py` — ניהול צ'אט LLM
- [ ] E3. `aibroker/agent/brain.py` — מוח הסוכן: think(), פענוח החלטות
- [ ] E4. `aibroker/agent/prompts.py` — פרומפטים למערכת ולמשתמש
- [ ] E5. `aibroker/agent/collector.py` — בניית Snapshot

### F — לולאת הסוכן (הליבה)
- [x] F1. `aibroker/agent/loop.py` — 38 בדיקות, 100% עברו (כולל מינוף, lookahead, חשבונאות)
- [x] F2. `aibroker/agent/fast_strategy.py` — 37 בדיקות, 100% עברו (SMA, RSI, ATR, מומנטום)
- [ ] F3. `aibroker/agent/guardian.py` — שומר: stop-loss, ניטור תיק
- [ ] F4. `aibroker/agent/persistence.py` — שמירה/שחזור מצב הסוכן
- [ ] F5. `aibroker/agent/alerts.py` — התראות Telegram

### G — Plan B (אסטרטגיות חלופיות)
- [ ] G1. `aibroker/planb/backtest/engine.py` — מנוע בקטסט
- [ ] G2. `aibroker/planb/sim/session.py` — סימולציה צעד-צעד
- [ ] G3. `aibroker/planb/strategies/ma_cross.py` — חציית ממוצעים נעים
- [ ] G4. `aibroker/planb/strategies/momentum.py` — אסטרטגיית מומנטום
- [ ] G5. `aibroker/planb/strategies/base.py` — בסיס אסטרטגיה
- [ ] G6. `aibroker/planb/strategies/registry.py` — רישום אסטרטגיות
- [ ] G7. `aibroker/planb/llm/decision.py` — החלטות LLM ל-Plan B
- [ ] G8. `aibroker/planb/config.py` — הגדרות Plan B
- [ ] G9. `aibroker/planb/data/us_bars.py` — נתוני שוק US
- [ ] G10. `aibroker/planb/execution/paper_runner.py` — הרצת נייר Plan B
- [ ] G11. `aibroker/planb/live/guards.py` — בקרות בטיחות Plan B
- [ ] G12. `aibroker/planb/results.py` — עיבוד תוצאות
- [ ] G13. `aibroker/planb/risk_state.py` — מצב סיכון Plan B

### H — Runner ואורקסטרציה
- [ ] H1. `aibroker/runner/orchestrator.py` — הרצת אסטרטגיות והזמנות
- [ ] H2. `aibroker/simulation/paper_autopilot.py` — בקטסט swing
- [ ] H3. `aibroker/simulation/demo_trades.py` — דאטה דמו

### I — אסטרטגיות כלליות
- [ ] I1. `aibroker/strategies/simple_rules.py` — כללים פשוטים
- [ ] I2. `aibroker/strategies/swing.py` — אסטרטגיית swing
- [ ] I3. `aibroker/strategies/regime.py` — זיהוי משטר שוק
- [ ] I4. `aibroker/strategies/base.py` — בסיס אסטרטגיה

### J — מצב ריצה
- [ ] J1. `aibroker/state/runtime.py` — RuntimeState snapshot

### K — ממשק WEB (שרת + ממשק)
- [ ] K1. `aibroker/web/server.py` — FastAPI routes, agent API
- [ ] K2. `aibroker/web/static/index.html` — ממשק משתמש (HTML/JS/CSS)
- [ ] K3. `aibroker/web/demo_data.py` — נתוני דמו לממשק
- [ ] K4. `aibroker/web/port_util.py` — כלי בדיקת פורט

### L — CLI והפעלה
- [ ] L1. `aibroker/cli.py` — שורת פקודה

---

## שלב 3: בדיקות אינטגרציה ו-E2E
- [ ] M1. בדיקת אינטגרציה: Data → Loop → Brain → Risk → Execution → State
- [ ] M2. בדיקות Playwright: לחצנים, מצבים, שגיאות בממשק

---

סה"כ רכיבים לבדיקה: **47 רכיבים + 2 בדיקות מערכת**
