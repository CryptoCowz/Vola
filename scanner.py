import os
import json
import time
import smtplib
import requests
import numpy as np
import yfinance as yf
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai
from google.genai import types

# ---------------------------------------------------------
# Configuration & Credentials
# ---------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")

STATE_FILE = "state.json"
CACHE_EXPIRY_SECONDS = 86400  # 24-hour deduplication window
WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD", "QQQ", "SPY"]

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
    current_time = time.time()
    return {k: v for k, v in state.items() if current_time - v < CACHE_EXPIRY_SECONDS}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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


def get_market_data(ticker):
    df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
    df_hourly = yf.download(ticker, period="5d", interval="1h", progress=False)
    
    # Flatten MultiIndex columns if present
    if hasattr(df_daily.columns, 'levels') and len(df_daily.columns.levels) > 1:
        df_daily.columns = df_daily.columns.get_level_values(0)
    if hasattr(df_hourly.columns, 'levels') and len(df_hourly.columns.levels) > 1:
        df_hourly.columns = df_hourly.columns.get_level_values(0)

    current_price = round(float(df_hourly['Close'].dropna().iloc[-1]), 2)
    hourly_atr = round(calculate_atr(df_hourly, period=14), 2)
    curr_vol, vol_rank = calculate_volatility_rank(df_daily)

    return {
        "ticker": ticker,
        "current_price": current_price,
        "hourly_atr": hourly_atr,
        "pulcini_atr_115_buffer": round(hourly_atr * 1.15, 2),
        "volatility_context": {
            "annualized_vol_pct": curr_vol,
            "volatility_rank_ivr_proxy": vol_rank,
            "regime": "High Vol (Mean Reversion Favored)" if vol_rank >= 50 else "Low Vol (Trend Favored)"
        },
        "balance_range": {
            "top_of_balance": round(float(df_hourly['High'].max()), 2),
            "bottom_of_balance": round(float(df_hourly['Low'].min()), 2),
            "high_volume_midpoint": round((float(df_hourly['High'].max()) + float(df_hourly['Low'].min())) / 2, 2)
        }
    }


def send_email_alert(setup):
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
        print("Email configuration incomplete. Skipping email.")
        return

    recipients = [e.strip() for e in EMAIL_RECEIVER.split(",") if e.strip()]
    if not recipients:
        return

    subject = f"🎯 Market Alert: {setup['ticker']} - {setup['setup_type']} ({setup['direction']})"
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: {'#2e7d32' if 'Long' in setup['direction'] else '#c62828'};">
          {setup['ticker']} Setup: {setup['setup_type']} ({setup['direction']})
        </h2>
        <table style="width: 100%; max-width: 550px; border-collapse: collapse;">
          <tr><td style="padding: 6px; font-weight: bold;">Key Level:</td><td>${setup['key_level']}</td></tr>
          <tr><td style="padding: 6px; font-weight: bold;">Trigger (+1.15 ATR):</td><td>${setup['trigger_price']}</td></tr>
          <tr><td style="padding: 6px; font-weight: bold;">Invalidation:</td><td>${setup['invalidation_price']}</td></tr>
          <tr><td style="padding: 6px; font-weight: bold;">Vol Rank:</td><td>{setup['vol_rank']}% ({setup['vol_regime']})</td></tr>
        </table>
        <p style="background: #f4f4f4; padding: 10px; border-left: 4px solid #0066cc; margin-top: 15px;">
          {setup['reasoning']}
        </p>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Market Scanner <{EMAIL_SENDER}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"Email delivered to: {', '.join(recipients)}")
    except Exception as e:
        print(f"Failed to send email: {e}")


def main():
    state = load_state()
    
    spy_df = yf.download("SPY", period="5d", interval="1d", progress=False)
    vix_df = yf.download("^VIX", period="5d", interval="1d", progress=False)
    
    if hasattr(spy_df.columns, 'levels') and len(spy_df.columns.levels) > 1:
        spy_df.columns = spy_df.columns.get_level_values(0)
    if hasattr(vix_df.columns, 'levels') and len(vix_df.columns.levels) > 1:
        vix_df.columns = vix_df.columns.get_level_values(0)

    spy_chg = spy_df['Close'].pct_change().dropna().iloc[-1]
    vix_val = vix_df['Close'].dropna().iloc[-1]
    macro_context = f"SPY 1-Day Change: {spy_chg * 100:.2f}% | VIX: {vix_val:.2f}"
    
    market_payload = [get_market_data(ticker) for ticker in WATCHLIST]
    
    prompt = f"""
    Context: {macro_context}
    Market Data: {json.dumps(market_payload, default=str)}
    Evaluate candidate setups using the 3-Layer Integrated Framework (Structure, ATR Displacement, IV Rank).
    Output a valid JSON array of qualified setups. If none qualify, return [].
    """

    system_instruction = """
    You are an expert Quantitative Trading Agent. Evaluate setups strictly.
    Return JSON array of objects with keys:
    ticker, direction, setup_type, key_level, trigger_price, invalidation_price, vol_rank, vol_regime, reasoning.
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        
        setups = json.loads(response.text)
        new_alerts = False
        
        for setup in setups:
            alert_id = f"{setup['ticker']}_{setup['setup_type']}_{setup['key_level']}".replace(" ", "_")
            if alert_id in state:
                print(f"Duplicate alert skipped: {alert_id}")
                continue
            
            send_email_alert(setup)
            state[alert_id] = time.time()
            new_alerts = True

        if new_alerts:
            save_state(state)
        print("Market scan completed successfully.")
            
    except Exception as e:
        print(f"Execution error: {e}")


if __name__ == "__main__":
    main()
