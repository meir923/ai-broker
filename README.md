# AI Broker (scaffold)

מימוש ראשוני לפי `.cursor/plans/מערכת_מסחר_אוטונומית_9d50099a.plan.md`.

## הפעלה קלה (Windows — בלי טרמינל)

1. פתח את התיקייה `ai broker` ב־Explorer.
2. **לחץ פעמיים על `START.vbs`**.
3. ייפתחו חלון שרת (שחור) + הדפדפן ל־`http://127.0.0.1:8765/`. **אל תסגור** את חלון השרת בזמן שימוש; לעצירה: `Ctrl+C` בחלון השחור.

אלטרנטיבה: `run_web.bat` (אותו רעיון, פחות אוטומציה).

### Playwriter (Cursor) — דפדפן אמיתי

ב־Cursor אפשר לשלוט בכרום דרך הרחבת **Playwriter** (MCP `user-playwriter`). דוגמה: ניווט ל־`http://127.0.0.1:8765/` ו־`snapshot` — רק אחרי שהשרת רץ וההרחבה מופעלת בלשונית.

### Playwright (בדיקות אוטומטיות בפרויקט)

```text
pip install -e ".[e2e]"
playwright install chromium
set AIBROKER_E2E=1
pytest tests/e2e -v
```

בלי `AIBROKER_E2E=1` בדיקות ה־E2E ידולגו; שאר הבדיקות (`tests/test_*.py`) רצות כרגיל.

## התקנה

```text
cd "ai broker"
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
```

אופציונלי ל-IBKR: `pip install -e ".[ibkr]"`  
לוח בקרה בדפדפן: `pip install -e ".[web]"`

## שימוש

```text
aibroker --profile config/profiles/paper_safe.yaml profile
aibroker --profile config/profiles/paper_safe.yaml run
aibroker --profile config/profiles/paper_safe.yaml chat
```

### לראות את התוכנה בדפדפן (ממשק גרפי מקומי)

```text
pip install -e ".[web]"
aibroker --profile config/profiles/paper_safe.yaml web
```

ייפתח דפדפן ב־`http://127.0.0.1:8765/` — לוח עם לשוניות (סקירה, סימולציה חיה, הגדרות, ריסק, Grok, גרפים, ביצוע). **סימולציה חיה** מפעילה `run-once` אמיתי מהשרת; קישור ישיר: `http://127.0.0.1:8765/#simulation`. אחרי עדכון קוד: עצור את השרת והפעל מחדש. לסגירה: `Ctrl+C` בחלון השרת.  
ללא פתיחה אוטומטית של הדפדפן (רק פקודה ידנית): `--no-browser`.

הדגל `--profile` חייב לבוא **לפני** שם הפקודה (`profile` / `run` / `chat` / `web`).

`run` / `chat` עדיין שלד; חיבור TWS ו-Grok ימולאו בשלבים הבאים.

## סודות

העתק `.env.example` ל־`.env` והגדר `GROK_API_KEY` כשנדרש. ל־`live` או `auto_within_risk` נדרש גם `I_ACCEPT_LIVE_RISK=true`.

## לא עובד? (Windows)

1. **הכי פשוט** — `START.vbs` (לחיצה כפולה). אם נחסם: לחץ ימני → Run with PowerShell על `Launch.ps1`.
2. **תיקייה** — אם מריצים ידנית, `cd` חייב להיות לתיקיית `ai broker`.
3. **חסרות ספריות** — `pip install -e ".[web]"` מתוך אותה תיקייה.
4. **פורט 8765 תפוס** (שרת ישן רץ) — סגור את החלון הישן או: `python -m aibroker.cli web --port 9876` ואז פתח `http://127.0.0.1:9876/`.
5. **חייבים כתובת HTTP** — הדפדפן צריך `http://127.0.0.1:8765/` ולא קובץ שנפתח ישירות מהדיסק.
6. **סימולציה** — בלשונית «סימולציה חיה» או `http://127.0.0.1:8765/#simulation` (רק כשהשרת רץ).
