"""Prompt templates for the AI trading agent."""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """אתה סוכן מסחר מקצועי שמנהל תיק השקעות מגוון בשוק האמריקאי.

כללים קשיחים:
1. בכל צעד תחזיר פעולות על לפחות 3 סימבולים שונים
2. תמיד שמור לפחות 5 פוזיציות פתוחות במקביל - פיזור הוא המפתח!
3. אם יש לך פחות מ-5 פוזיציות פתוחות → חובה לפתוח חדשות
4. אם מזומן > 30% מההון → חובה לפתוח פוזיציות חדשות מיד
5. מקסימום 12% מההון בפוזיציה אחת
6. אל תסגור פוזיציה שפתחת באותו יום אלא אם ההפסד > 3%
7. כמות מניות = מספר שלם חיובי

פעולות:
- buy = קנייה (לונג)
- sell = מכירת לונג קיים
- short = פתיחת שורט (מכירה בחסר)
- cover = סגירת שורט קיים

אסטרטגיה:
- מניה עם טרנד UP ו-RSI < 70 → buy
- מניה עם טרנד DOWN ו-RSI > 30 → short
- מניה עם טרנד שהתהפך מול הפוזיציה → סגור (sell/cover)
- מניה בצד → סקאלפינג קטן

סיבה קצרה בעברית לכל פעולה.

ענה אך ורק ב-JSON:
{
  "actions": [
    {"symbol": "XXX", "action": "buy|sell|short|cover", "quantity": 10, "reason": "סיבה"}
  ],
  "market_view": "תיאור קצר בעברית",
  "risk_note": "הערת סיכון בעברית"
}"""


RISK_INSTRUCTIONS = {
    "low": "רמת סיכון נמוכה: מקסימום 15% מההון בפוזיציה. העדף מניות יציבות (SPY, QQQ, MSFT). אין שורטים. סטופ הדוק 10%.",
    "medium": "רמת סיכון בינונית: מקסימום 20% מההון בפוזיציה. מותר שורט במקרים ברורים. סטופ 15%.",
    "high": "רמת סיכון מוגברת: מקסימום 25% מההון בפוזיציה. שורטים מותרים. פוזיציות ריכוזיות אגרסיביות. סטופ רחב 20%.",
}


def format_user_prompt(snapshot: dict[str, Any]) -> str:
    lines: list[str] = []

    risk = snapshot.get("risk_level", "medium")
    if risk in RISK_INSTRUCTIONS:
        lines.append(f"*** {RISK_INSTRUCTIONS[risk]} ***")
        lines.append("")

    clock = snapshot.get("clock", {})
    date = snapshot.get("date", "")
    lines.append(f"--- {date} ---")
    lines.append(f"NY: {clock.get('ny_time', '?')} | IL: {clock.get('il_time', '?')} | {clock.get('status', '?')}")
    lines.append("")

    port = snapshot.get("portfolio", {})
    lines.append("--- Portfolio ---")
    lines.append(f"Cash: ${port.get('cash', 0):,.0f} | Equity: ${port.get('equity', 0):,.0f} | PnL: ${port.get('pnl', 0):+,.0f} ({port.get('pnl_pct', 0):+.1f}%)")

    positions = port.get("positions", [])
    open_count = len(positions)
    lines.append(f"Open positions: {open_count}")
    if positions:
        for p in positions:
            upl = p.get("unrealized_pnl", 0)
            side = "LONG" if p.get("qty", 0) >= 0 else "SHORT"
            lines.append(
                f"  {p['symbol']} {side} {abs(p['qty']):.0f}x @ ${p['avg_cost']:.2f} (now ${p['current_price']:.2f}, {'+'if upl>=0 else ''}{upl:.0f})"
            )
    if open_count < 5:
        lines.append(f"*** WARNING: Only {open_count} positions open. You MUST open more to reach at least 5! ***")
    lines.append("")

    news = snapshot.get("news", [])
    if news:
        lines.append("--- News (live) ---")
        for i, h in enumerate(news[:15], 1):
            sym = h.get("symbol", "")
            title = h.get("title", "")
            lines.append(f"{i}. [{sym}] {title}")
        lines.append("")
    else:
        lines.append("--- No news available, decide based on technicals only ---")
        lines.append("")

    tech = snapshot.get("technicals", {})
    if tech:
        lines.append("--- Technicals ---")
        for sym, t in tech.items():
            lines.append(
                f"{sym}: ${t.get('price', 0):.2f} | MA20: ${t.get('ma20', 0):.2f} | "
                f"RSI14: {t.get('rsi14', '?')} | ATR14: {t.get('atr14', '?')} | "
                f"trend: {t.get('trend', '?')}"
            )

    return "\n".join(lines)
