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
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")  # Comma-separated if multiple
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL") # Optional

STATE_FILE = "state.json"
CACHE_EXPIRY_SECONDS = 86400  # 24-hour deduplication window

WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD", "QQQ", "SPY"]

client = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------
# State Management (Prevent Duplicate Alerts)
# ---------------------------------------------------------
def load_state():
    """Loads state.json and prunes records older than 24 hours."""
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
    """Saves updated state back to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------
# Technical & Volatility Calculations
# ---------------------------------------------------------
def calculate_atr(df, period=14):
    """Calculates standard Average True Range (ATR)."""
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    atr = tr.rolling(window=period).mean().iloc[-1]
    return float(atr)


def calculate_volatility_rank(df_daily, window=252):
    """Calculates 30-day realized volatility and ranks it over 1 year (Sosnoff IVR proxy)."""
    log_ret = np.log(df_daily['Close'] / df_daily['Close'].shift(1))
    rolling_vol = log_ret.rolling(window=20).std() * np.sqrt(252) * 100
    
    recent_vol = rolling_vol.iloc[-1]
    vol_min = rolling_vol.tail(window).min()
    vol_max = rolling_vol.tail(window).max()
    
    if vol_max == vol_min:
        ivr_proxy = 50.0
    else:
        ivr_proxy = ((recent_vol - vol_min) / (vol_max - vol_min)) * 100
        
    return round(float(recent_vol), 2), round(float(ivr_proxy), 1)


def get_market_data(ticker):
    """Extracts structural price levels, ATR buffers, and volatility metrics."""
    df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
    df_hourly = yf.download(ticker, period="5d", interval="1h", progress=False)
    
    current_price = round(float(df_hourly['Close'].iloc[-1]), 2)
    hourly_atr = round(calculate_atr(df_hourly, period=14), 2)
    atr_buffer = round(hourly_atr * 1.15, 2)  # Pulcini ATR + 15% rule
    
    balance_high = round(float(df_hourly['High'].max()), 2)
    balance_low = round(float(df_hourly['Low'].min()), 2)
    poc_midpoint = round((balance_high + balance_low) / 2, 2)

    curr_vol, vol_rank = calculate_volatility_rank(df_daily)

    return {
        "ticker": ticker,
        "current_price": current_price,
        "hourly_atr": hourly_atr,
        "pulcini_atr_115_buffer": atr_buffer,
        "volatility_context": {
            "annualized_vol_pct": curr_vol,
            "volatility_rank_ivr_proxy": vol_rank,
            "regime": "High Vol (Mean Reversion Favored)" if vol_rank >= 50 else "Low Vol (Trend Favored)"
        },
        "balance_range": {
            "top_of_balance": balance_high,
            "bottom_of_balance": balance_low,
            "high_volume_midpoint": poc_midpoint
        },
        "recent_hourly_closes": df_hourly['Close'].tail(5).round(2).to_dict()
    }


# ---------------------------------------------------------
# Notification Handlers (Gmail + Discord)
# ---------------------------------------------------------
def send_email_alert(setup):
    """Dispatches formatted HTML alerts to all specified Gmail recipients."""
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECEIVER):
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
        <table style="width: 100%; max-width: 550px; border-collapse: collapse; margin-bottom: 15px;">
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Key Level / Node:</td>
            <td style="padding: 8px;">${setup['key_level']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Trigger Price (+1.15 ATR):</td>
            <td style="padding: 8px;">${setup['trigger_price']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Invalidation:</td>
            <td style="padding: 8px;">${setup['invalidation_price']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #ddd;">
            <td style="padding: 8px; font-weight: bold;">Volatility Context:</td>
            <td style="padding: 8px;">{setup['vol_rank']}% ({setup['vol_regime']})</td>
          </tr>
        </table>
        <h3 style="margin-bottom: 6px;">Execution Thesis:</h3>
        <p style="background: #f9f9f9; padding: 12px; border-left: 4px solid #0066cc; border-radius: 4px;">
          {setup['reasoning']}
        </p>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Vola Market Scanner <{EMAIL_SENDER}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"Email successfully sent to: {', '.join(recipients)}")
    except Exception as e:
        print(f"Failed to send email alert: {e}")


def send_discord_alert(setup):
    """Optional webhook dispatcher for Discord."""
    if not DISCORD_WEBHOOK:
        return
    color = 3066993 if "Long" in setup.get('direction', '') else 15158332
    content = {
        "embeds": [{
            "title": f"🎯 Setup: {setup['ticker']} ({setup['setup_type']})",
            "color": color,
            "fields": [
                {"name": "Direction", "value": setup['direction'], "inline": True},
                {"name": "Key Level", "value": f"${setup['key_level']}", "inline": True},
                {"name": "Trigger (+1.15 ATR)", "value": f"${setup['trigger_price']}", "inline": True},
                {"name": "Invalidation", "value": f"${setup['invalidation_price']}", "inline": True},
                {"name": "Vol Rank (IVR)", "value": f"{setup['vol_rank']}% ({setup['vol_regime']})", "inline": True},
                {"name": "Execution Thesis", "value": setup['reasoning'], "inline": False}
            ]
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK, json=content, timeout=10)
    except Exception as e:
        print(f"Discord alert failed: {e}")


# ---------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------
def main():
    state = load_state()
    
    # 1. Macro Context Fetch
    spy = yf.download("SPY", period="5d", interval="1d", progress=False)['Close'].pct_change().iloc[-1]
    vix = yf.download("^VIX", period="5d", interval="1d", progress=False)['Close'].iloc[-1]
    macro_context = f"SPY 1-Day Change: {spy * 100:.2f}% | VIX Index Level: {vix:.2f}"
    
    # 2. Watchlist Aggregation
    market_payload = [get_market_data(ticker) for ticker in WATCHLIST]
    
    # 3. Gemini Prompting
    prompt = f"""
    Broader Context: {macro_context}
    
    Market, Balance & Volatility Data:
    {json.dumps(market_payload, default=str)}
    
    Evaluate candidate setups using the 3-Layer Integrated Framework:
    1. Structure (Brando/Pulcini): Identify tests/rejections at Balance Edges, Reclaims, or Failed Breakouts.
    2. Confirmation: Price MUST displace away from the volume/balance node by at least `pulcini_atr_115_buffer`.
    3. Volatility Context (Sosnoff): 
       - If Vol Rank >= 50: Prioritize mean-reverting reclaims and fading extremes.
       - If Vol Rank < 30: Require strong continuation structures; pass on weak counter-trend probes.

    Output a valid JSON array of qualified setups. If none qualify, return [].
    """

    system_instruction = """
    You are an expert Quantitative Price Action & Order Flow Trading Agent.
    Evaluate the data strictly against structural balance, ATR displacement (+1.15 ATR), and IV Rank context.
    Return JSON array of objects with keys:
    ticker, direction (Long/Short), setup_type, key_level, trigger_price, invalidation_price, vol_rank, vol_regime, reasoning.
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
            
            # Deduplication Check
            if alert_id in state:
                print(f"Skipping duplicate alert: {alert_id}")
                continue
            
            send_email_alert(setup)
            send_discord_alert(setup)
            
            state[alert_id] = time.time()
            new_alerts = True

        if new_alerts:
            save_state(state)
            
    except Exception as e:
        print(f"Scanner error: {e}")


if __name__ == "__main__":
    main()