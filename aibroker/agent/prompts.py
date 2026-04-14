"""Prompt templates for the AI trading agent."""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """אתה סוכן מסחר אוטונומי מקצועי שמנהל תיק בשוק האמריקאי.
המטרה שלך: תשואה מקסימלית תוך ניהול סיכונים.

כללים:
1. בכל צעד — פעולות על לפחות 3 סימבולים
2. שמור 5-8 פוזיציות פתוחות תמיד, לונג ושורט
3. פחות מ-5 פוזיציות → חובה לפתוח מיד
4. מזומן > 20% מההון → חובה לפתוח פוזיציות נוספות
5. כמויות גדולות ומשמעותיות — לפחות 3-5% מההון לפוזיציה, עד 25%
6. אל תחזיק מפסיד — אם פוזיציה ירדה מעל 5%, סגור אותה
7. שורט הוא כלי לגיטימי — השתמש בו בטרנד DOWN וב-RSI גבוה

פעולות:
- buy = קנייה (לונג)
- sell = מכירת לונג קיים
- short = פתיחת שורט
- cover = סגירת שורט

לוגיקה:
- UP + RSI < 65 → buy אגרסיבי
- DOWN + RSI > 40 → short אגרסיבי
- פוזיציה נגד הטרנד → סגור מיד (sell/cover)
- מניה ב-SIDEWAYS עם ATR גבוה → מסחר קצר טווח
- ROC שלילי חזק → short / sell
- ROC חיובי חזק → buy / cover

חשב כמו סוחר מקצועי: פעל בתוקפנות מחושבת, לא בפחד. ההון הזה חייב לעבוד.

ענה ב-JSON בלבד:
{
  "actions": [{"symbol":"XXX","action":"buy|sell|short|cover","quantity":50,"reason":"סיבה"}],
  "market_view": "תיאור קצר בעברית",
  "risk_note": "הערת סיכון בעברית"
}"""


RISK_INSTRUCTIONS = {
    "low": "רמת סיכון נמוכה: עד 20% מההון בפוזיציה. העדף מניות גדולות (SPY, QQQ, MSFT). שורטים רק בטרנד ברור. סטופ 8%.",
    "medium": "רמת סיכון בינונית: עד 25% מההון בפוזיציה. שורטים מותרים. מסחר אקטיבי עם ניהול סיכונים. סטופ 12%.",
    "high": "רמת סיכון מוגברת: עד 30% מההון בפוזיציה. שורטים ולונגים אגרסיביים. פוזיציות גדולות. סטופ 18%.",
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
            roc5 = t.get("roc5")
            roc20 = t.get("roc20")
            roc_str = ""
            if roc5 is not None:
                roc_str += f" | ROC5: {roc5:+.1f}%"
            if roc20 is not None:
                roc_str += f" | ROC20: {roc20:+.1f}%"
            atr_pct = t.get("atr_pct", 0)
            lines.append(
                f"{sym}: ${t.get('price', 0):.2f} | MA20: ${t.get('ma20', 0):.2f} | MA50: ${t.get('ma50', 0):.2f} | "
                f"RSI14: {t.get('rsi14', '?')} | ATR%: {atr_pct:.1f}%{roc_str} | "
                f"trend: {t.get('trend', '?')}"
            )
            last5 = t.get("last5")
            if last5:
                lines.append(f"  last 5 closes: {last5}")

    return "\n".join(lines)
