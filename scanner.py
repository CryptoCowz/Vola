import os
import json
import time
import smtplib
import unicodedata
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai
from google.genai import types

# ---------------------------------------------------------
# Credentials & String Sanitization
# ---------------------------------------------------------
def clean_env(val):
    if not val:
        return ""
    return unicodedata.normalize("NFKD", val).replace("\xa0", "").strip()

GEMINI_API_KEY = clean_env(os.environ.get("GEMINI_API_KEY"))
ALPACA_KEY = clean_env(os.environ.get("ALPACA_API_KEY"))
ALPACA_SECRET = clean_env(os.environ.get("ALPACA_SECRET_KEY"))
EMAIL_SENDER = clean_env(os.environ.get("EMAIL_SENDER"))
EMAIL_APP_PASSWORD = clean_env(os.environ.get("EMAIL_APP_PASSWORD")).replace(" ", "")
EMAIL_RECEIVER = clean_env(os.environ.get("EMAIL_RECEIVER"))

STATE_FILE = "state.json"
CACHE_EXPIRY_SECONDS = 86400  # 24-hour deduplication window

# High-liquidity multi-sector watchlist
UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "AVGO", "SMCI", "ARM", "PLTR", "COIN", "MARA", "JPM", "BAC", "GS",
    "XOM", "CVX", "LLY", "UNH", "NFLX", "DIS", "BA", "CAT", "NKE", "COST"
]

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY secret. Please add it to GitHub Secrets.")

client = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------
# State Management & Sandbox Ledger
# ---------------------------------------------------------
def load_state():
    """Loads alert cache and persistent $100 simulated sandbox ledger."""
    default_state = {
        "alerts": {},
        "sandbox": {
            "initial_balance": 100.00,
            "current_balance": 100.00,
            "start_date": datetime.now().strftime("%b %d, %Y"),
            "total_trades": 0,
            "hours_saved": 0.0,
            "open_positions": []
        }
    }
    
    if not os.path.exists(STATE_FILE):
        return default_state

    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            
        if "alerts" not in data or "sandbox" not in data:
            return {
                "alerts": {k: v for k, v in data.items() if isinstance(v, (int, float))},
                "sandbox": default_state["sandbox"]
            }
            
        now = time.time()
        data["alerts"] = {k: v for k, v in data["alerts"].items() if now - v < CACHE_EXPIRY_SECONDS}
        return data
    except Exception:
        return default_state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------
# Market Data Engines (Alpaca with yfinance Fallback)
# ---------------------------------------------------------
def get_alpaca_bars(symbol, timeframe="1Hour", days_back=30):
    if not (ALPACA_KEY and ALPACA_SECRET):
        return pd.DataFrame()

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    start_dt = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"timeframe": timeframe, "start": start_dt, "limit": 1000, "feed": "iex", "sort": "asc"}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            return pd.DataFrame()
        bars = res.json().get("bars", [])
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame(bars)
        df.rename(columns={"t": "Timestamp", "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}, inplace=True)
        return df
    except Exception:
        return pd.DataFrame()


def get_yfinance_bars(symbol, interval="1h", period="5d"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            return pd.DataFrame()
        if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return pd.DataFrame()


def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    tr = np.maximum(high - low, np.maximum((high - close).abs(), (low - close).abs()))
    atr = tr.rolling(window=period).mean().dropna()
    return float(atr.iloc[-1]) if not atr.empty else 1.0


def calculate_volatility_rank(df_daily):
    log_ret = np.log(df_daily['Close'] / df_daily['Close'].shift(1))
    rolling_vol = (log_ret.rolling(window=20).std() * np.sqrt(252) * 100).dropna()
    if rolling_vol.empty:
        return 20.0, 50.0
    recent_vol = rolling_vol.iloc[-1]
    vol_min = rolling_vol.min()
    vol_max = rolling_vol.max()
    ivr_proxy = 50.0 if vol_max == vol_min else ((recent_vol - vol_min) / (vol_max - vol_min)) * 100
    return round(float(recent_vol), 2), round(float(ivr_proxy), 1)


def process_ticker(ticker):
    df_hourly = get_alpaca_bars(ticker, timeframe="1Hour", days_back=15)
    if df_hourly.empty or len(df_hourly) < 15:
        df_hourly = get_yfinance_bars(ticker, interval="1h", period="5d")

    df_daily = get_alpaca_bars(ticker, timeframe="1Day", days_back=365)
    if df_daily.empty or len(df_daily) < 50:
        df_daily = get_yfinance_bars(ticker, interval="1d", period="1y")

    if df_hourly.empty or len(df_hourly) < 10:
        return None

    current_price = round(float(df_hourly['Close'].dropna().iloc[-1]), 2)
    hourly_atr = round(calculate_atr(df_hourly, period=14), 2)
    curr_vol, vol_rank = calculate_volatility_rank(df_daily) if not df_daily.empty else (20.0, 50.0)

    bal_high = round(float(df_hourly['High'].max()), 2)
    bal_low = round(float(df_hourly['Low'].min()), 2)
    poc = round((bal_high + bal_low) / 2, 2)

    return {
        "ticker": ticker,
        "current_price": current_price,
        "hourly_atr": hourly_atr,
        "pulcini_atr_115_buffer": round(hourly_atr * 1.15, 2),
        "volatility_context": {
            "vol_rank_ivr": vol_rank,
            "regime": "High Vol" if vol_rank >= 50 else "Low Vol"
        },
        "balance_range": {
            "top": bal_high,
            "bottom": bal_low,
            "poc": poc
        }
    }


# ---------------------------------------------------------
# Autonomous Gamified Simulation Engine
# ---------------------------------------------------------
def update_sandbox_ledger(state, qualifying_setups):
    """
    Simulates live trade execution, tracks active positions against live prices,
    and updates the compounded $100 bankroll in state.json.
    """
    sandbox = state["sandbox"]
    
    # Increment screen time saved (0.5 hrs per automated scan)
    sandbox["hours_saved"] = round(sandbox.get("hours_saved", 0.0) + 0.5, 1)
    
    # 1. Evaluate & Settle Open Positions
    active_positions = []
    for pos in sandbox.get("open_positions", []):
        ticker = pos["ticker"]
        df_now = get_alpaca_bars(ticker, timeframe="1Hour", days_back=2)
        if df_now.empty:
            df_now = get_yfinance_bars(ticker, interval="1h", period="2d")
            
        if df_now.empty:
            active_positions.append(pos)
            continue

        latest_price = float(df_now['Close'].dropna().iloc[-1])
        entry = pos["entry_price"]
        stop = pos["invalidation"]
        target = pos["target_price"]
        is_long = "Long" in pos["direction"]
        
        target_hit = (latest_price >= target) if is_long else (latest_price <= target)
        stop_hit = (latest_price <= stop) if is_long else (latest_price >= stop)

        if target_hit:
            pnl_gain = round(pos["risk_amount"] * pos["rr_ratio"], 2)
            sandbox["current_balance"] = round(sandbox["current_balance"] + pnl_gain, 2)
            print(f"[Sandbox] Target reached for {ticker}! Closed with +${pnl_gain}")
        elif stop_hit:
            pnl_loss = pos["risk_amount"]
            sandbox["current_balance"] = round(max(0.0, sandbox["current_balance"] - pnl_loss), 2)
            print(f"[Sandbox] Invalidation hit for {ticker}. Closed with -${pnl_loss}")
        else:
            active_positions.append(pos)

    sandbox["open_positions"] = active_positions

    # 2. Enter Top Ranked Trade into Simulation (Max 3 concurrent positions)
    if qualifying_setups and len(sandbox["open_positions"]) < 3:
        top_pick = qualifying_setups[0]
        holding_tickers = [p["ticker"] for p in sandbox["open_positions"]]
        
        if top_pick["ticker"] not in holding_tickers:
            try:
                entry_p = float(top_pick["trigger_price"])
                stop_p = float(top_pick["invalidation_price"])
                risk_pct = 0.02  # 2% risk rule
                risk_usd = round(sandbox["current_balance"] * risk_pct, 2)
                
                stop_dist = abs(entry_p - stop_p)
                target_p = entry_p + (stop_dist * 2.5) if "Long" in top_pick["direction"] else entry_p - (stop_dist * 2.5)

                new_pos = {
                    "id": f"{top_pick['ticker']}_{int(time.time())}",
                    "ticker": top_pick["ticker"],
                    "direction": top_pick["direction"],
                    "entry_price": entry_p,
                    "invalidation": stop_p,
                    "target_price": round(target_p, 2),
                    "risk_amount": risk_usd,
                    "rr_ratio": 2.5,
                    "timestamp": time.time()
                }
                sandbox["open_positions"].append(new_pos)
                sandbox["total_trades"] += 1
                print(f"[Sandbox] Entered trade: {top_pick['ticker']} ({top_pick['direction']})")
            except Exception as e:
                print(f"[Sandbox] Error opening position: {e}")

    return sandbox


# ---------------------------------------------------------
# Mobile-Optimized VOLA Digest Dispatch (BCC)
# ---------------------------------------------------------
def send_digest_email(top_setups, sandbox):
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
        print("Email configuration incomplete. Skipping email.")
        return

    recipients = [clean_env(e) for e in EMAIL_RECEIVER.split(",") if clean_env(e)]
    if not recipients:
        return

    pnl = round(sandbox["current_balance"] - sandbox["initial_balance"], 2)
    pnl_pct = round((pnl / sandbox["initial_balance"]) * 100, 1)
    pnl_color = "#10b981" if pnl >= 0 else "#ef4444"
    pnl_sign = "+" if pnl >= 0 else ""

    subject = f"⚡ VOLA Briefing: Top {len(top_setups)} Market Setups & Autonomous Report"
    
    cards_html = ""
    for idx, s in enumerate(top_setups, 1):
        is_long = "Long" in s.get('direction', '')
        badge_bg = "#ecfdf5" if is_long else "#fef2f2"
        badge_text = "#065f46" if is_long else "#991b1b"
        border_accent = "#10b981" if is_long else "#ef4444"
        
        cards_html += f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 5px solid {border_accent}; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 1px solid #f1f5f9; padding-bottom: 8px;">
            <div>
              <span style="font-size: 18px; font-weight: 800; color: #0f172a;">#{idx} {s['ticker']}</span>
              <span style="background-color: {badge_bg}; color: {badge_text}; font-size: 12px; font-weight: 700; padding: 3px 8px; border-radius: 9999px; margin-left: 6px; text-transform: uppercase;">
                {s['direction']} • {s['setup_type']}
              </span>
            </div>
            <div style="font-size: 13px; font-weight: 700; color: #4338ca;">
              R:R {s.get('risk_reward', '2.5:1')}
            </div>
          </div>

          <table style="width: 100%; font-size: 13px; color: #475569; margin-bottom: 12px; border-collapse: collapse;">
            <tr>
              <td style="padding: 4px 0; width: 50%;"><strong>Key Level:</strong> <span style="color: #0f172a;">${s['key_level']}</span></td>
              <td style="padding: 4px 0; width: 50%;"><strong>Trigger (+1.15 ATR):</strong> <span style="color: #0f172a;">${s['trigger_price']}</span></td>
            </tr>
            <tr>
              <td style="padding: 4px 0;"><strong>Invalidation:</strong> <span style="color: #0f172a;">${s['invalidation_price']}</span></td>
              <td style="padding: 4px 0;"><strong>IV Rank:</strong> <span style="color: #0f172a;">{s['vol_rank']}%</span></td>
            </tr>
          </table>

          <div style="background-color: #f8fafc; border-radius: 6px; padding: 10px 12px; font-size: 13px; color: #334155; line-height: 1.45;">
            <strong style="color: #1e293b;">Thesis:</strong> {s['reasoning']}
          </div>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 16px 8px; color: #1e293b;">
        <div style="max-width: 600px; margin: 0 auto;">
          
          <!-- Header Banner -->
          <div style="background: linear-gradient(135deg, #1e1b2e 0%, #2d2244 100%); padding: 24px 20px; border-radius: 12px 12px 0 0; text-align: left;">
            <div style="display: inline-block; background-color: #38bdf8; color: #0f172a; font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; margin-bottom: 8px;">
              Agentic Intelligence
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.5px;">
              VOLA Market Briefing
            </h1>
            <p style="color: #cbd5e1; margin: 6px 0 0 0; font-size: 14px; line-height: 1.4;">
              Eliminating cognitive fatigue and emotional friction through autonomous execution.
            </p>
          </div>

          <!-- Market Content Container -->
          <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-top: none; padding: 16px; border-radius: 0 0 12px 12px;">
            
            <!-- GAMIFIED $100 AUTONOMOUS SANDBOX CARD -->
            <div style="background: #ffffff; border: 1px solid #c7d2fe; border-radius: 10px; padding: 16px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(67, 56, 202, 0.08);">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; border-bottom: 1px solid #f1f5f9; padding-bottom: 8px;">
                <span style="font-size: 12px; font-weight: 800; color: #4338ca; text-transform: uppercase; letter-spacing: 0.5px;">
                  🎮 The $100 Autonomous Sandbox
                </span>
                <span style="font-size: 11px; color: #64748b; font-weight: 600;">Started: {sandbox['start_date']}</span>
              </div>
              
              <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px;">
                <div>
                  <span style="font-size: 24px; font-weight: 800; color: #0f172a;">${sandbox['current_balance']:.2f}</span>
                  <span style="font-size: 13px; font-weight: 700; color: {pnl_color}; margin-left: 6px;">
                    {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct}%)
                  </span>
                </div>
                <div style="text-align: right; font-size: 12px; color: #475569;">
                  <strong>{sandbox.get('total_trades', 0)}</strong> Trades • <strong>{sandbox.get('hours_saved', 0.0)} hrs</strong> Saved
                </div>
              </div>

              <div style="background: #eef2ff; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #3730a3; line-height: 1.4;">
                <strong>CFO Insight:</strong> Tracking how a $100 allocation performs under strict ±1.15 ATR discipline without human panic selling or decision fatigue.
              </div>
            </div>

            <!-- Setups Feed -->
            <div style="margin-bottom: 12px; font-size: 14px; font-weight: 700; color: #334155;">
              Top {len(top_setups)} High-Conviction Opportunities
            </div>
            {cards_html}

            <!-- Footer -->
            <div style="text-align: center; margin-top: 24px; padding-top: 16px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 12px; line-height: 1.5;">
              <p style="margin: 0; font-weight: 600; color: #64748b;">
                "Automating the survival mechanism so you can reclaim your life energy."[cite: 1, 4, 11]
              </p>
              <p style="margin: 4px 0 0 0;">
                VOLA Autonomous Fiduciary • Algorithmic Liberty[cite: 1, 4, 11]
              </p>
            </div>
          </div>

        </div>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"VOLA Update <{EMAIL_SENDER}>"
    msg["To"] = "VOLA Subscribers <undisclosed-recipients:;>"
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"VOLA Mobile Digest successfully dispatched to {len(recipients)} recipients via BCC.")
    except Exception as e:
        print(f"Failed to send VOLA email: {e}")


# ---------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------
def main():
    state = load_state()
    print("Initiating real-time market scan across watchlist...")
    
    candidates = []
    for ticker in UNIVERSE:
        data = process_ticker(ticker)
        if data:
            candidates.append(data)
            
    print(f"Successfully processed {len(candidates)} active symbols.")

    if not candidates:
        print("No market data available to scan.")
        return

    prompt = f"""
    Real-Time Market Data:
    {json.dumps(candidates, default=str)}

    Rank and select the Top 20 Most Asymmetric Trade Setups based on:
    1. Structure (Brando/Pulcini): Reclaims, Failed Breakouts, Rejections at Balance Edges.
    2. Confirmation: Minimum price displacement of `pulcini_atr_115_buffer` away from the level.
    3. Profit Potential: Prioritize high R:R (minimum 2.0:1) toward opposite balance POC/extremes.
    4. Volatility (Sosnoff): High IV Rank (>=50) for mean-reversion reclaims.

    Return JSON array of max 20 objects sorted by potential profitability.
    """

    system_instruction = """
    You are an elite Quantitative Portfolio & Momentum Trader.
    Return JSON array of objects with keys:
    ticker, direction, setup_type, key_level, trigger_price, invalidation_price, risk_reward, vol_rank, reasoning.
    """

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        
        ranked_setups = json.loads(response.text)

        # Update the live simulation ledger
        sandbox = update_sandbox_ledger(state, ranked_setups)

        # Always dispatch the hourly digest during market runs
        if ranked_setups:
            send_digest_email(ranked_setups, sandbox)
            save_state(state)
        else:
            print("No qualifying market setups found during this scan.")
            
    except Exception as e:
        print(f"Evaluation error: {e}")


if __name__ == "__main__":
    main()
