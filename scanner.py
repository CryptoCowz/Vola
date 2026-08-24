import os
import io
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
# Credentials & Environment Sanitization
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

# High-liquidity universe spanning tech, semiconductors, financials, retail, energy, and indices
UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "AVGO", "SMCI", "ARM", "PLTR", "COIN", "MARA", "JPM", "BAC", "GS",
    "XOM", "CVX", "LLY", "UNH", "NFLX", "DIS", "BA", "CAT", "NKE", "COST"
]

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY secret. Please add it to GitHub Secrets.")

client = genai.Client(api_key=GEMINI_API_KEY)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except Exception:
        return {}
    now = time.time()
    return {k: v for k, v in state.items() if now - v < CACHE_EXPIRY_SECONDS}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------
# Dual Market Data Engine (Alpaca with yfinance Fallback)
# ---------------------------------------------------------
def get_alpaca_bars(symbol, timeframe="1Hour", days_back=30):
    """Fetches real-time candles from Alpaca with proper start time parameters."""
    if not (ALPACA_KEY and ALPACA_SECRET):
        return pd.DataFrame()

    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET
    }
    
    start_dt = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "timeframe": timeframe,
        "start": start_dt,
        "limit": 1000,
        "feed": "iex",
        "sort": "asc"
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            print(f"[Alpaca Note] {symbol} returned status {res.status_code}: {res.text[:100]}")
            return pd.DataFrame()
        
        bars = res.json().get("bars", [])
        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df.rename(columns={"t": "Timestamp", "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}, inplace=True)
        return df
    except Exception as e:
        print(f"[Alpaca Error] {symbol}: {e}")
        return pd.DataFrame()


def get_yfinance_bars(symbol, interval="1h", period="5d"):
    """Fallback engine using yfinance with multi-index flattening."""
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            return pd.DataFrame()
        if hasattr(df.columns, 'levels') and len(df.columns.levels) > 1:
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"[yfinance Error] {symbol}: {e}")
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
    """Attempts Alpaca fetch first; falls back to yfinance if unavailable."""
    # 1. Hourly Bars
    df_hourly = get_alpaca_bars(ticker, timeframe="1Hour", days_back=15)
    if df_hourly.empty or len(df_hourly) < 15:
        df_hourly = get_yfinance_bars(ticker, interval="1h", period="5d")

    # 2. Daily Bars (for IVR calculation)
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
# Notification Pipeline (VOLA Update with BCC)
# ---------------------------------------------------------
def send_digest_email(top_setups):
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
        print("Email configuration incomplete. Skipping email.")
        return

    recipients = [clean_env(e) for e in EMAIL_RECEIVER.split(",") if clean_env(e)]
    if not recipients:
        print("No valid recipient addresses found.")
        return

    subject = f"VOLA Update: Top {len(top_setups)} Market Setups & Volatility Report"
    
    rows_html = ""
    for idx, s in enumerate(top_setups, 1):
        color = "#2e7d32" if "Long" in s.get('direction', '') else "#c62828"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #e0e0e0;">
          <td style="padding: 8px; font-weight: bold;">#{idx} {s['ticker']}</td>
          <td style="padding: 8px; color: {color}; font-weight: bold;">{s['direction']} ({s['setup_type']})</td>
          <td style="padding: 8px;">${s['key_level']}</td>
          <td style="padding: 8px;">${s['trigger_price']}</td>
          <td style="padding: 8px;">${s['invalidation_price']}</td>
          <td style="padding: 8px; font-weight: bold;">{s.get('risk_reward', '2.5:1')}</td>
          <td style="padding: 8px;">{s['vol_rank']}%</td>
          <td style="padding: 8px; font-size: 13px; color: #555;">{s['reasoning']}</td>
        </tr>
        """

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #222; max-width: 950px; margin: auto;">
        <div style="background-color: #0f172a; padding: 16px 20px; border-radius: 6px 6px 0 0;">
          <h2 style="color: #38bdf8; margin: 0; font-size: 20px;">
            ⚡ VOLA Market Intelligence Digest
          </h2>
          <p style="color: #94a3b8; margin: 4px 0 0 0; font-size: 13px;">
            Top {len(top_setups)} Opportunities (Price Action Structure, ATR Displacement & Volatility Rank)
          </p>
        </div>
        <div style="border: 1px solid #e2e8f0; border-top: none; padding: 15px; border-radius: 0 0 6px 6px;">
          <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
            <thead>
              <tr style="background-color: #f8fafc; border-bottom: 2px solid #e2e8f0;">
                <th style="padding: 8px;">Rank / Ticker</th>
                <th style="padding: 8px;">Bias</th>
                <th style="padding: 8px;">Key Level</th>
                <th style="padding: 8px;">Trigger (+1.15 ATR)</th>
                <th style="padding: 8px;">Invalidation</th>
                <th style="padding: 8px;">Est. R:R</th>
                <th style="padding: 8px;">IV Rank</th>
                <th style="padding: 8px;">Thesis</th>
              </tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
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
        print(f"VOLA Update successfully dispatched via BCC to {len(recipients)} recipients.")
    except Exception as e:
        print(f"Failed to send VOLA email: {e}")


# ---------------------------------------------------------
# Main Execution
# ---------------------------------------------------------
def main():
    state = load_state()
    print("Initiating market scan across watchlist...")
    
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
        new_setups_for_digest = []

        for s in ranked_setups:
            alert_id = f"{s['ticker']}_{s['setup_type']}_{s['key_level']}".replace(" ", "_")
            if alert_id in state:
                continue
            new_setups_for_digest.append(s)
            state[alert_id] = time.time()

        if new_setups_for_digest:
            send_digest_email(new_setups_for_digest)
            save_state(state)
        else:
            print("No new qualifying setups passed the 24h deduplication filter.")
            
    except Exception as e:
        print(f"Evaluation error: {e}")


if __name__ == "__main__":
    main()
