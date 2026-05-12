"""
Smart Options Flow Alert Bot
=============================
100% FREE to run. No paid APIs needed.
- yfinance    -- free options data
- Groq AI     -- free AI analysis (console.groq.com)
- Telegram    -- free alerts

SETUP:
1. pip install yfinance requests python-telegram-bot apscheduler
2. Fill in your 3 keys below
3. Run: python smart_options_bot.py

ALERT FORMAT:
  Stock:   NVDA
  Type:    CALL
  Strike:  $950
  Expiry:  May 2 (4 days)
  Why:     3 sharp sentences explaining the trade

WIN/LOSS TRACKER:
- Logs every alert to tracker.json
- Checks outcome at expiry automatically
- Posts weekly scoreboard every Friday 4 PM
"""

import yfinance as yf
import requests
import asyncio
import logging
import json
import os
from datetime import datetime, date, timedelta
from telegram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
#  CONFIG -- Fill these 3 keys in
# ─────────────────────────────────────────
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL")

MIN_DAILY_ALERTS = 2    # Guaranteed minimum alerts per day
HIGH_SCORE       = 75   # Post immediately when found
LOW_SCORE        = 60   # Backup pool to fill minimum quota
POST_HOUR        = 15   # EOD fill at 3:30 PM ET
POST_MINUTE      = 30

WATCHLIST = [
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA",
    "MSFT", "META", "AMZN", "AMD", "GOOGL",
    "JPM", "GS", "BAC", "XLF", "IWM",
    "LCID", "WMT", "DIS", "KO", "NFLX"
]

TRACKER_FILE = "tracker.json"

# ─────────────────────────────────────────
#  GROQ AI -- Free, fast, no cost
# ─────────────────────────────────────────

def ask_groq(prompt: str) -> str:
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama3-70b-8192",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.7,
            },
            timeout=15
        )
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq error: {e}")
        return "Unusual institutional activity detected on this contract."


def generate_why(trade: dict, score: int, reasons: list) -> str:
    """Generate 3 sharp sentences explaining why this trade is notable."""
    rsn = "\n".join(f"- {r}" for r in reasons)

    prompt = f"""You are an options flow analyst. Write exactly 3 short sentences explaining why this trade is notable.
Each sentence must be under 20 words. Be direct and confident. No bullet points, no disclaimers.
Just 3 plain sentences separated by newlines.

Trade: {trade['ticker']} {trade['type']} ${trade['strike']} expiring {trade['expiry']} ({trade['days_to_expiry']} DTE)
Premium: ${trade['premium']:,} | Vol/OI: {trade['oi_ratio']}x | IV: {trade['iv']}% | Score: {score}/100

Key signals:
{rsn}"""

    return ask_groq(prompt)


# ─────────────────────────────────────────
#  WIN/LOSS TRACKER
# ─────────────────────────────────────────

def load_tracker():
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    return {"alerts": [], "summary": {"wins": 0, "losses": 0, "pending": 0}}


def save_tracker(data):
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)


def log_alert(trade: dict, score: int):
    tracker = load_tracker()
    entry = {
        "id":            f"{trade['ticker']}-{trade['type']}-{trade['strike']}-{trade['expiry']}",
        "ticker":        trade["ticker"],
        "type":          trade["type"],
        "strike":        trade["strike"],
        "expiry":        trade["expiry"],
        "spot_at_alert": trade["spot_price"],
        "score":         score,
        "alerted_at":    datetime.now().isoformat(),
        "outcome":       "pending",
        "spot_at_expiry": None,
        "pct_move":      None,
    }
    # Avoid duplicate entries
    if not any(a["id"] == entry["id"] for a in tracker["alerts"]):
        tracker["alerts"].append(entry)
        tracker["summary"]["pending"] += 1
        save_tracker(tracker)


def get_current_price(ticker: str) -> float:
    try:
        t = yf.Ticker(ticker)
        return t.fast_info["last_price"]
    except:
        return 0


def check_outcomes():
    tracker = load_tracker()
    updated = False
    today   = date.today().isoformat()

    for alert in tracker["alerts"]:
        if alert["outcome"] != "pending":
            continue
        if alert["expiry"] > today:
            continue

        spot_now = get_current_price(alert["ticker"])
        if not spot_now:
            continue

        entry    = alert["spot_at_alert"]
        pct_move = round((spot_now - entry) / entry * 100, 2)

        alert["spot_at_expiry"] = spot_now
        alert["pct_move"]       = pct_move
        alert["outcome"]        = "WIN" if (
            (alert["type"] == "CALL" and spot_now > entry) or
            (alert["type"] == "PUT"  and spot_now < entry)
        ) else "LOSS"

        tracker["summary"]["pending"] = max(0, tracker["summary"]["pending"] - 1)
        tracker["summary"][alert["outcome"].lower() + "s"] += 1
        updated = True
        logger.info(f"Settled: {alert['id']} -> {alert['outcome']} ({pct_move:+.2f}%)")

    if updated:
        save_tracker(tracker)

    return tracker


def build_scoreboard() -> str:
    tracker  = load_tracker()
    summary  = tracker["summary"]
    wins     = summary.get("wins", 0)
    losses   = summary.get("losses", 0)
    pending  = summary.get("pending", 0)
    total    = wins + losses
    win_rate = round(wins / total * 100) if total > 0 else 0

    settled = [a for a in tracker["alerts"] if a["outcome"] != "pending"]
    recent  = settled[-10:][::-1]

    lines = [
        "WEEKLY TRACK RECORD",
        "=" * 30,
        f"Total Alerts:  {total + pending}",
        f"Wins:          {wins}",
        f"Losses:        {losses}",
        f"Win Rate:      {win_rate}%",
        f"Pending:       {pending}",
        "=" * 30,
        "Last 10 Results:",
    ]

    for a in recent:
        icon  = "W" if a["outcome"] == "WIN" else "L"
        arrow = "+" if (a.get("pct_move") or 0) >= 0 else ""
        lines.append(
            f"[{icon}] {a['ticker']} {a['type']} ${a['strike']} "
            f"({arrow}{a.get('pct_move', 0):.1f}%)"
        )

    lines += ["", "Not financial advice. Educational only."]
    return "\n".join(lines)


# ─────────────────────────────────────────
#  SCORING ENGINE (0-100)
# ─────────────────────────────────────────

def score_trade(trade: dict):
    score   = 0
    reasons = []

    vol    = trade["volume"]
    ratio  = trade["oi_ratio"]
    prem   = trade["premium"]
    iv     = trade["iv"]
    dte    = trade["days_to_expiry"]
    ctype  = trade["type"]
    strike = trade["strike"]
    spot   = trade["spot_price"]

    # 1. Vol/OI ratio
    if ratio >= 20:
        score += 25
        reasons.append(f"Vol/OI is {ratio}x -- extreme unusual activity")
    elif ratio >= 10:
        score += 20
        reasons.append(f"Vol/OI is {ratio}x -- well above normal")
    elif ratio >= 5:
        score += 12
        reasons.append(f"Vol/OI is {ratio}x -- elevated activity")
    elif ratio >= 3:
        score += 6
        reasons.append(f"Vol/OI is {ratio}x -- above average")

    # 2. Premium size
    if prem >= 1_000_000:
        score += 20
        reasons.append(f"${prem:,} premium -- institutional size bet")
    elif prem >= 500_000:
        score += 15
        reasons.append(f"${prem:,} premium -- large conviction")
    elif prem >= 250_000:
        score += 10
        reasons.append(f"${prem:,} premium -- significant positioning")
    elif prem >= 100_000:
        score += 5
        reasons.append(f"${prem:,} premium -- notable size")

    # 3. OTM = directional bet
    otm = abs(strike - spot) / max(spot, 1) * 100
    if ctype == "CALL" and strike > spot and 2 <= otm <= 8:
        score += 15
        reasons.append(f"OTM call {otm:.1f}% out -- directional bet, not a hedge")
    elif ctype == "PUT" and strike < spot and 2 <= otm <= 8:
        score += 15
        reasons.append(f"OTM put {otm:.1f}% out -- directional bearish bet")
    elif otm < 2:
        score += 5
        reasons.append("Near-the-money -- could be directional or hedge")

    # 4. Expiry timing
    if 7 <= dte <= 21:
        score += 15
        reasons.append(f"{dte} days to expiry -- short-dated signals conviction")
    elif 21 < dte <= 45:
        score += 10
        reasons.append(f"{dte} days to expiry -- medium term positioning")
    elif dte < 7:
        score += 5
        reasons.append(f"{dte} days to expiry -- aggressive short-term bet")
    elif dte > 60:
        score -= 5
        reasons.append(f"{dte} days to expiry -- long dated, possible hedge")

    # 5. Volume
    if vol >= 10000:
        score += 10
        reasons.append(f"{vol:,} contracts -- very high absolute volume")
    elif vol >= 5000:
        score += 7
        reasons.append(f"{vol:,} contracts -- strong volume")
    elif vol >= 2000:
        score += 4
        reasons.append(f"{vol:,} contracts -- notable volume")

    # 6. IV context
    if iv < 40:
        score += 5
        reasons.append(f"IV at {iv}% -- low vol, efficient entry point")
    elif iv > 80:
        score -= 5
        reasons.append(f"IV at {iv}% -- elevated, likely near earnings")

    return min(score, 100), reasons


# ─────────────────────────────────────────
#  DATA FETCHING (yfinance -- free)
# ─────────────────────────────────────────

def get_spot(ticker: str) -> float:
    try:
        t = yf.Ticker(ticker)
        return t.fast_info["last_price"]
    except:
        return 0


def get_options(ticker: str) -> list:
    try:
        t      = yf.Ticker(ticker)
        spot   = get_spot(ticker)
        cutoff = date.today() + timedelta(days=60)
        trades = []

        for exp_str in t.options:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            if exp_date < date.today() or exp_date > cutoff:
                continue

            chain = t.option_chain(exp_str)

            for row in chain.calls.itertuples():
                t2 = row_to_trade(row, "CALL", exp_str, spot, ticker)
                if t2:
                    trades.append(t2)

            for row in chain.puts.itertuples():
                t2 = row_to_trade(row, "PUT", exp_str, spot, ticker)
                if t2:
                    trades.append(t2)

        trades.sort(key=lambda x: x["volume"], reverse=True)
        return trades[:100]

    except Exception as e:
        logger.error(f"yfinance error {ticker}: {e}")
        return []


def row_to_trade(row, ctype: str, exp_str: str, spot: float, ticker: str):
    try:
        vol    = int(getattr(row, "volume", 0) or 0)
        oi     = int(getattr(row, "openInterest", 1) or 1)
        last   = float(getattr(row, "lastPrice", 0) or 0)
        iv     = float(getattr(row, "impliedVolatility", 0) or 0) * 100
        strike = float(row.strike)
        dte    = (datetime.strptime(exp_str, "%Y-%m-%d").date() - date.today()).days

        moneyness = spot / strike if strike > 0 else 1
        if ctype == "CALL":
            delta = round(min(max(moneyness - 0.5, 0.05), 0.95), 2)
        else:
            delta = round(min(max(0.5 - moneyness, -0.95), -0.05), 2)

        return {
            "ticker":         ticker,
            "type":           ctype,
            "strike":         strike,
            "expiry":         exp_str,
            "days_to_expiry": dte,
            "volume":         vol,
            "open_int":       oi,
            "oi_ratio":       round(vol / max(oi, 1), 1),
            "last_price":     last,
            "premium":        int(vol * last * 100),
            "delta":          delta,
            "iv":             round(iv, 1),
            "spot_price":     spot,
        }
    except:
        return None


# ─────────────────────────────────────────
#  ALERT FORMATTING
# ─────────────────────────────────────────

def format_alert(trade: dict, score: int, why: str, num: int) -> str:
    expiry_fmt = datetime.strptime(trade["expiry"], "%Y-%m-%d").strftime("%b %d")
    label      = "HIGH CONVICTION" if score >= HIGH_SCORE else "WATCHLIST"
    side       = "BULLISH" if trade["type"] == "CALL" else "BEARISH"
    emoji      = "GREEN" if trade["type"] == "CALL" else "RED"

    return (
        f"[{emoji}] ALERT #{num} -- {label} {side}\n"
        f"\n"
        f"Stock:    {trade['ticker']}\n"
        f"Type:     {trade['type']}\n"
        f"Strike:   ${trade['strike']}\n"
        f"Expiry:   {expiry_fmt} ({trade['days_to_expiry']} days)\n"
        f"Premium:  ${trade['premium']:,}\n"
        f"Vol/OI:   {trade['oi_ratio']}x\n"
        f"Score:    {score}/100\n"
        f"\n"
        f"Why:\n{why}\n"
        f"\n"
        f"Not financial advice. Educational only."
    )


# ─────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────

daily_candidates = []
posted_today     = 0
last_reset       = None
alert_counter    = 0


def reset_daily():
    global daily_candidates, posted_today, last_reset
    daily_candidates = []
    posted_today     = 0
    last_reset       = date.today()
    logger.info("Daily state reset.")


# ─────────────────────────────────────────
#  SEND ALERT
# ─────────────────────────────────────────

async def send_alert(bot: Bot, candidate: dict):
    global posted_today, alert_counter

    trade   = candidate["trade"]
    score   = candidate["score"]
    reasons = candidate["reasons"]

    why           = generate_why(trade, score, reasons)
    alert_counter += 1
    msg           = format_alert(trade, score, why, alert_counter)

    try:
        await bot.send_message(chat_id=TELEGRAM_CHANNEL, text=msg)
        candidate["posted"] = True
        posted_today        += 1
        log_alert(trade, score)
        logger.info(f"Alert #{alert_counter} sent: {candidate['id']} score={score}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

    await asyncio.sleep(2)


# ─────────────────────────────────────────
#  SCAN
# ─────────────────────────────────────────

async def scan(bot: Bot):
    global daily_candidates

    if last_reset != date.today():
        reset_daily()

    logger.info(f"Scanning {len(WATCHLIST)} tickers...")

    for ticker in WATCHLIST:
        contracts = get_options(ticker)

        for trade in contracts:
            if not trade:
                continue
            if trade["volume"] < 500 or trade["premium"] < 50_000:
                continue

            score, reasons = score_trade(trade)

            if score >= LOW_SCORE:
                tid = f"{trade['ticker']}-{trade['type']}-{trade['strike']}-{trade['expiry']}"
                if not any(c["id"] == tid for c in daily_candidates):
                    entry = {
                        "id":      tid,
                        "trade":   trade,
                        "score":   score,
                        "reasons": reasons,
                        "posted":  False,
                    }
                    daily_candidates.append(entry)
                    logger.info(f"Candidate: {tid} score={score}")

                    # Fire immediately if high conviction
                    if score >= HIGH_SCORE:
                        await send_alert(bot, entry)

        await asyncio.sleep(1)

    daily_candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Scan done. Candidates: {len(daily_candidates)}")


# ─────────────────────────────────────────
#  END OF DAY -- Guarantee minimum 2
# ─────────────────────────────────────────

async def end_of_day(bot: Bot):
    if not daily_candidates:
        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL,
            text="Markets quiet today -- no notable flow detected. Back tomorrow."
        )
        return

    unposted  = [c for c in daily_candidates if not c.get("posted")]
    high_tier = [c for c in unposted if c["score"] >= HIGH_SCORE]
    fill_tier = [c for c in unposted if LOW_SCORE <= c["score"] < HIGH_SCORE]

    # Post any missed high conviction trades
    for c in high_tier:
        await send_alert(bot, c)

    # Fill to minimum if needed
    needed = max(0, MIN_DAILY_ALERTS - posted_today)
    for c in fill_tier[:needed]:
        await send_alert(bot, c)

    logger.info(f"EOD complete. Total alerts today: {posted_today}")


# ─────────────────────────────────────────
#  WEEKLY SCOREBOARD
# ─────────────────────────────────────────

async def post_scoreboard(bot: Bot):
    check_outcomes()
    board = build_scoreboard()
    try:
        await bot.send_message(chat_id=TELEGRAM_CHANNEL, text=board)
        logger.info("Scoreboard posted.")
    except Exception as e:
        logger.error(f"Scoreboard error: {e}")


# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────

async def send_test_alert(bot):
    msg = (
        "[TEST ALERT] Bot is connected and working!\n"
        "\n"
        "Stock:    NVDA\n"
        "Type:     CALL\n"
        "Strike:   $950\n"
        "Expiry:   May 2 (4 days)\n"
        "Premium:  $1,240,000\n"
        "Vol/OI:   14.2x\n"
        "Score:    88/100\n"
        "\n"
        "Why:\n"
        "Institutional money dropped $1.2M on short-dated OTM calls.\n"
        "Vol/OI ratio of 14x means almost all volume is fresh new positioning.\n"
        "Delta confirms this is a directional bet, not a hedge.\n"
        "\n"
        "This is a TEST. Real alerts start at market open 9:30 AM ET.\n"
        "Not financial advice. Educational only."
    )
    try:
        await bot.send_message(chat_id=TELEGRAM_CHANNEL, text=msg)
        logger.info("Test alert sent!")
    except Exception as e:
        logger.error(f"Test alert failed: {e}")


async def main():
    bot       = Bot(token=TELEGRAM_TOKEN)
    scheduler = AsyncIOScheduler(timezone="America/New_York")

    # Fire test alert on startup to confirm Telegram is connected
    await send_test_alert(bot)

    # Scan every 15 min during market hours
    scheduler.add_job(scan, "cron", day_of_week="mon-fri",
                      hour="9-15", minute="*/15", args=[bot])

    # EOD post at 3:30 PM ET -- guarantee minimum 2
    scheduler.add_job(end_of_day, "cron", day_of_week="mon-fri",
                      hour=POST_HOUR, minute=POST_MINUTE, args=[bot])

    # Reset daily state at 9:30 AM ET
    scheduler.add_job(reset_daily, "cron", day_of_week="mon-fri",
                      hour=9, minute=30)

    # Weekly scoreboard every Friday 4 PM ET
    scheduler.add_job(post_scoreboard, "cron", day_of_week="fri",
                      hour=16, minute=0, args=[bot])

    # Daily outcome check at 5 PM ET
    scheduler.add_job(check_outcomes, "cron", day_of_week="mon-fri",
                      hour=17, minute=0)

    scheduler.start()
    logger.info("Bot is live. Scanning every 15 min. EOD at 3:30 PM. Scoreboard Fridays 4 PM.")

    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
