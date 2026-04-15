"""Prompt templates for the AI trading agent (Two-Tier Architecture).

Tier 1 (code) screens candidates via fast_strategy.py.
Tier 2 (Grok) acts as Chief Analyst: sentiment, news, macro, final decisions.
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """אתה מנהל קרן גידור מבריק שמקבל החלטות סופיות לגבי מניות בשוק האמריקאי.

המערכת הטכנית כבר סרקה את השוק וזיהתה מועמדים (candidates) לקנייה/שורט על בסיס מומנטום, RSI, ממוצעים נעים ו-ATR.
תפקידך: לבדוק את הסיפור מאחורי כל מועמד ולהחליט מי באמת שווה השקעה.

תחומי אחריות:
1. סנטימנט: סרוק את הכותרות וחפש דגלים אדומים (תביעות, דוחות מאכזבים, אזהרות רווח) או דגלים ירוקים (הכנסות שוברות שיא, שותפויות, מוצרים חדשים)
2. מאקרו: העריך את מצב השוק הכללי — האם זה יום למתקפה או להגנה?
3. סינון: פסול מועמדים עם סנטימנט שלילי או חדשות רעות, גם אם הטכניקלס טובים
4. גודל: קבע כמויות לפי רמת הביטחון שלך — יותר ביטחון = פוזיציה גדולה יותר

עקרונות:
- פעל רק כשיש יתרון (edge) ברור — אין חובה לפעול בכל צעד
- אם אין הזדמנות ראויה, מותר להחזיר actions ריק ולהשאיר מזומן
- פוזיציה מפסידה מעל 5% → שקול לסגור (sell/cover)
- סנטימנט שלילי + טרנד DOWN = שורט אגרסיבי
- סנטימנט חיובי + טרנד UP = לונג אגרסיבי
- סנטימנט סותר את הטכניקלס = הקטן פוזיציה או דלג
- בשוק לא ברור, העדף מזומן על פוזיציות מפוקפקות

פעולות:
- buy = קנייה (לונג)
- sell = מכירת לונג קיים
- short = פתיחת שורט
- cover = סגירת שורט

ענה ב-JSON בלבד:
{
  "regime": "bullish|bearish|neutral",
  "market_view": "הערכת שוק כללית בעברית",
  "risk_note": "הערת סיכון בעברית",
  "aggression": "conservative|normal|aggressive",
  "cash_bias": "deploy|hold|raise",
  "avoid_symbols": ["סימבולים שיש לא לגעת בהם כרגע"],
  "priority_symbols": ["סימבולים שיש יתרון מיוחד לפעול עליהם"],
  "actions": [{"symbol":"XXX","action":"buy|sell|short|cover","quantity":50,"reason":"סיבה כולל ניתוח סנטימנט"}]
}"""


RISK_INSTRUCTIONS = {
    "low": "רמת סיכון נמוכה: מינוף 2:1. עד 20% מההון בפוזיציה. העדף מועמדים עם סנטימנט חיובי ברור. סטופ 8%.",
    "medium": "רמת סיכון בינונית: מינוף 3:1. עד 25% מההון בפוזיציה. מותר לפעול גם על סנטימנט מעורב. סטופ 12%.",
    "high": "רמת סיכון מוגברת: מינוף 4:1. עד 30% מההון בפוזיציה. פעל אגרסיבית על כל סנטימנט ברור. סטופ 18%.",
}


MACRO_REGIME_PROMPT = """אתה אנליסט מאקרו-כלכלי. על בסיס הכותרות הבאות, קבע את משטר השוק להיום.

ענה ב-JSON בלבד:
{
  "regime": "bullish|bearish|neutral",
  "confidence": 0.0-1.0,
  "reasoning": "הסבר קצר בעברית"
}"""


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

    regime = snapshot.get("regime")
    if regime:
        lines.append(f"*** Market Regime: {regime.upper()} ***")
        lines.append("")

    port = snapshot.get("portfolio", {})
    lines.append("--- Portfolio ---")
    bp = port.get("buying_power", 0)
    lev = port.get("leverage", 2.0)
    lines.append(f"Cash: ${port.get('cash', 0):,.0f} | Equity: ${port.get('equity', 0):,.0f} | PnL: ${port.get('pnl', 0):+,.0f} ({port.get('pnl_pct', 0):+.1f}%)")
    lines.append(f"Buying Power: ${bp:,.0f} | Leverage: {lev:.0f}:1")

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
    lines.append("")

    # Candidates from algorithmic screening (Tier 1)
    candidates = snapshot.get("candidates", [])
    if candidates:
        lines.append("--- Candidates (pre-screened by algorithm) ---")
        for c in candidates:
            score = c.get("momentum", 0)
            sent = c.get("sentiment", 0)
            sent_label = "POS" if sent > 0.2 else "NEG" if sent < -0.2 else "NEU"
            sent_summary = c.get("sentiment_summary", "")
            direction = c.get("direction", "buy")
            lines.append(
                f"{c['symbol']}: ${c.get('price', 0):.2f} | momentum: {score:+.1f}% | "
                f"RSI: {c.get('rsi', '?'):.0f} | trend: {c.get('trend', '?')} | "
                f"sentiment: {sent:+.2f} ({sent_label}) | suggested: {direction}"
            )
            if sent_summary:
                lines.append(f"  >> {sent_summary}")
        lines.append("")

    # News headlines
    news = snapshot.get("news", [])
    if news:
        lines.append("--- Headlines ---")
        for i, h in enumerate(news[:20], 1):
            sym = h.get("symbol", "")
            title = h.get("title", "")
            lines.append(f"{i}. [{sym}] {title}")
        lines.append("")
    else:
        lines.append("--- No news available ---")
        lines.append("")

    # Existing positions with technicals for management decisions
    tech = snapshot.get("technicals", {})
    pos_syms = {p["symbol"] for p in positions}
    pos_tech = {sym: t for sym, t in tech.items() if sym in pos_syms}
    if pos_tech:
        lines.append("--- Current Positions Technicals ---")
        for sym, t in pos_tech.items():
            roc5 = t.get("roc5")
            roc_str = f" | ROC5: {roc5:+.1f}%" if roc5 is not None else ""
            lines.append(
                f"{sym}: RSI {t.get('rsi14', '?')} | trend: {t.get('trend', '?')}{roc_str}"
            )
        lines.append("")

    return "\n".join(lines)
