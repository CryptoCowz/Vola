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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai
from google.genai import types

# ---------------------------------------------------------
# Environment & String Sanitization
# ---------------------------------------------------------
def clean_env(val):
    if not val:
        return ""
    return unicodedata.normalize("NFKD", val).replace("\xa0", "").strip()

GEMINI_API_KEY = clean_env(os.environ.get("GEMINI_API_KEY"))
EMAIL_SENDER = clean_env(os.environ.get("EMAIL_SENDER"))
EMAIL_APP_PASSWORD = clean_env(os.environ.get("EMAIL_APP_PASSWORD")).replace(" ", "")
EMAIL_RECEIVER = clean_env(os.environ.get("EMAIL_RECEIVER"))
DISCORD_WEBHOOK = clean_env(os.environ.get("DISCORD_WEBHOOK_URL"))

STATE_FILE = "state.json"
CACHE_EXPIRY_SECONDS = 86400

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY secret.")

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


def get_sp500_tickers():
    """Fetches liquid S&P 500 tickers with custom User-Agent to avoid 403 blocks."""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        tables = pd.read_html(io.StringIO(response.text))
        df = tables[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        print(f"Successfully retrieved {len(tickers)} S&P 500 tickers.")
        return tickers
    except Exception as e:
        print(f"Failed to fetch S&P 500 list, using fallback watchlist: {e}")
        return ["AAPL", "NVDA", "TSLA", "MSFT", "AMD", "META", "AMZN", "GOOGL", "AVGO", "QQQ", "SPY"]


def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    tr = np.maximum(high - low, np.maximum((high - close).abs(), (low - close).abs()))
    atr = tr.rolling(window=period).mean().dropna()
    return float(atr.iloc[-1]) if not atr.empty else 1.0


def calculate_volatility_rank(df_daily, window=252):
    log_ret = np.log(df_daily['Close'] / df_daily['Close'].shift(1))
    rolling_vol = (log_ret.rolling(window=20).std() * np.sqrt(252) * 100).dropna()
    if rolling_vol.empty:
        return 20.0, 50.0
    recent_vol = rolling_vol.iloc[-1]
    vol_min = rolling_vol.tail(window).min()
    vol_max = rolling_vol.tail(window).max()
    ivr_proxy = 50.0 if vol_max == vol_min else ((recent_vol - vol_min) / (vol_max - vol_min)) * 100
    return round(float(recent_vol), 2), round(float(ivr_proxy), 1)


def analyze_ticker(ticker):
    """Processes market structure, ATR, and balance levels."""
    try:
        df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
        df_hourly = yf.download(ticker, period="5d", interval="1h", progress=False)
        
        if df_daily.empty or df_hourly.empty or len(df_hourly) < 15:
            return None

        if hasattr(df_daily.columns, 'levels') and len(df_daily.columns.levels) > 1:
            df_daily.columns = df_daily.columns.get_level_values(0)
        if hasattr(df_hourly.columns, 'levels') and len(df_hourly.columns.levels) > 1:
            df_hourly.columns = df_hourly.columns.get_level_values(0)

        current_price = round(float(df_hourly['Close'].dropna().iloc[-1]), 2)
        hourly_atr = round(calculate_atr(df_hourly, period=14), 2)
        curr_vol, vol_rank = calculate_volatility_rank(df_daily)

        bal_high = round(float(df_hourly['High'].max()), 2)
        bal_low = round(float(df_hourly['Low'].min()), 2)
        poc = round((bal_high + bal_low) / 2, 2)

        near_high = abs(current_price - bal_high) / bal_high <= 0.025
        near_low = abs(current_price - bal_low) / bal_low <= 0.025
        if not (near_high or near_low):
            return None

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
    except Exception:
        return None


def send_digest_email(top_setups):
    """Sends a single consolidated Top 20 opportunities digest."""
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
        return

    recipients = [e.strip() for e in EMAIL_RECEIVER.split(",") if e.strip()]
    if not recipients:
        return

    subject = f"🚀 Market Scan: Top {len(top_setups)} Price Action & Volatility Opportunities"
    
    rows_html = ""
    for idx, s in enumerate(top_setups, 1):
        color = "#2e7d32" if "Long" in s.get('direction', '') else "#c62828"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #e0e0e0;">
          <td style="padding: 10px; font-weight: bold;">#{idx} {s['ticker']}</td>
          <td style="padding: 10px; color: {color}; font-weight: bold;">{s['direction']} ({s['setup_type']})</td>
          <td style="padding: 10px;">${s['key_level']}</td>
          <td style="padding: 10px;">${s['trigger_price']}</td>
          <td style="padding: 10px;">${s['invalidation_price']}</td>
          <td style="padding: 10px; font-weight: bold;">{s.get('risk_reward', '2.5:1')}</td>
          <td style="padding: 10px;">{s['vol_rank']}%</td>
          <td style="padding: 10px; font-size: 13px; color: #555;">{s['reasoning']}</td>
        </tr>
        """

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5; color: #222; max-width: 950px; margin: auto;">
        <h2 style="color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 8px;">
          Hourly Market Opportunities Digest (Top {len(top_setups)})
        </h2>
        <p style="color: #666;">
          Ranked by asymmetric Profitability Potential (Risk-to-Reward, Structural Edge & Volatility Mean Reversion).
        </p>
        <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
          <thead>
            <tr style="background-color: #f1f3f4; border-bottom: 2px solid #ccc;">
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
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Market Alpha Bot <{EMAIL_SENDER}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"Top {len(top_setups)} digest successfully sent to {', '.join(recipients)}")
    except Exception as e:
        print(f"Failed to send digest email: {e}")


def main():
    state = load_state()
    print("Initiating full market scan across S&P 500...")
    
    universe = get_sp500_tickers()
    candidates = []
    
    for ticker in universe:
        data = analyze_ticker(ticker)
        if data:
            candidates.append(data)
    
    print(f"Identified {len(candidates)} candidates interacting at key structural edges.")
    
    if not candidates:
        print("No candidate stocks currently at structural balance edges.")
        return

    # Benchmark Context
    spy_df = yf.download("SPY", period="5d", interval="1d", progress=False)
    vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
    
    if hasattr(spy_df.columns, 'levels') and len(spy_df.columns.levels) > 1:
        spy_df.columns = spy_df.columns.get_level_values(0)
    if hasattr(vix_df.columns, 'levels') and len(vix_df.columns.levels) > 1:
        vix_df.columns = vix_df.columns.get_level_values(0)

    spy_chg = spy_df['Close'].pct_change().dropna().iloc[-1]
    vix_val = vix_df['Close'].dropna().iloc[-1]
    macro_context = f"SPY 1D Change: {spy_chg * 100:.2f}% | VIX: {vix_val:.2f}"

    prompt = f"""
    Context: {macro_context}
    Filtered Market Candidates at Balance Edges:
    {json.dumps(candidates, default=str)}

    Rank and extract the Top 20 Most Asymmetric Trade Setups based on:
    1. Structure (Brando/Pulcini): Support Reclaims, Failed Breakouts, Rejections at 5-day Balance Edges.
    2. Confirmation: Minimum price displacement of `pulcini_atr_115_buffer` away from the level.
    3. Profit Potential & R:R: Prioritize setups targeting the opposite balance boundary or high-volume POC (minimum 2.0:1 R:R).
    4. Volatility Context (Sosnoff): Prioritize high IV Rank (>=50) for mean reversion reclaims.

    Return JSON array of max 20 objects sorted from highest profitability potential to lowest.
    """

    system_instruction = """
    You are an elite Quantitative Portfolio & Momentum Trader.
    Evaluate candidate data strictly, compute realistic Risk-to-Reward (R:R), and rank the Top 20 setups.
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
            print("All top ranked setups were already alerted within the last 24 hours.")
            
    except Exception as e:
        print(f"Evaluation error: {e}")


if __name__ == "__main__":
    main()
