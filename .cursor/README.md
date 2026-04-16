# תיקיית `.cursor` בפרויקט ai-broker

## מה זה "Claude Code" / מודל פתוח ב-Cursor?

- **Cursor** יכול להציג כמה מוצרים/מודלים (למשל Composer, צ'אט, או חיבור חיצוני). השם "Claude" מגיע מ־**Anthropic** — אם יש חלון או הרחבה בשם Claude, זה **הגדרת IDE** ולא חלק מקוד הפרויקט הזה.
- **במאגר הזה אין קבצי קונפיגורציה של Claude** — החיפוש ב-repo לא מצא `claude` / `anthropic` בקבצים רלוונטיים.

## מה כן יש כאן?

| נתיב | משמעות |
|------|--------|
| `plans/מערכת_מסחר_אוטונומית_*.plan.md` | **תכנית ישנה מ-Cursor Plans** (IBKR כברירת מחדל, todos ישנים). **לא משקפת את המצב הנוכחי** של ai-broker (Alpaca, Grok, `aibroker/agent/…`). |

## איפה "אמת" הפרויקט?

- קוד: `aibroker/`
- README הראשי בשורש הrepo (אם קיים)
- עבודה אחרונה: שכבת מדיניות פנימית — `aibroker/agent/meta_policy.py`, `intent_normalizer.py`, `mini_allocator.py`, `approval.py`

## המלצה

- אל תסמוך על תוכנית ב־`plans/` ללא השוואה לקוד.
- אם רוצים תכנית מעודכנת: ליצור Plan חדש ב-Cursor מתוך `master` הנוכחי או לעדכן את הקובץ הקיים ידנית.
