import os
import json
import time
import requests
import numpy as np
import yfinance as yf
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
STATE_FILE = "state.json"
CACHE_EXPIRY_SECONDS = 86400  # 24 hours

WATCHLIST = ["AAPL", "NVDA", "TSLA", "MSFT", "AMD", "QQQ", "SPY"]

def load_state():
    """Loads previous alerts from state.json and prunes expired entries."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except Exception:
        return {}
    
    current_time = time.time()
    # Retain entries less than 24 hours old
    return {k: v for k, v in state.items() if current_time - v < CACHE_EXPIRY_SECONDS}

def save_state(state):
    """Writes the updated state dict to state.json."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    tr = np.maximum(high - low, np.maximum((high - close).abs(), (low - close).abs()))
    return float(tr.rolling(window=period).mean().iloc[-1])

def calculate_volatility_rank(df_daily, window=252):
    log_ret = np.log(df_daily['Close'] / df_daily['Close'].shift(1))
    rolling_vol = log_ret.rolling(window=20).std() * np.sqrt(252) * 100
    recent_vol = rolling_vol.iloc[-1]
    vol_min = rolling_vol.tail(window).min()
    vol_max = rolling_vol.tail(window).max()
    ivr_proxy = 50.0 if vol_max == vol_min else ((recent_vol - vol_min) / (vol_max - vol_min)) * 100
    return round(float(recent_vol), 2), round(float(ivr_proxy), 1)

def get_market_data(ticker):
    df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
    df_hourly = yf.download(ticker, period="5d", interval="1h", progress=False)
    
    current_price = round(float(df_hourly['Close'].iloc[-1]), 2)
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
        },
        "recent_hourly_closes": df_hourly['Close'].tail(5).round(2).to_dict()
    }

def send_alert(setup):
    if not DISCORD_WEBHOOK:
        return
    color = 3066993 if "Long" in setup.get('direction', '') else 15158332
    content = {
        "embeds": [{
            "title": f"🎯 Multi-Framework Trade Setup: {setup['ticker']} ({setup['setup_type']})",
            "color": color,
            "fields": [
                {"name": "Direction", "value": setup['direction'], "inline": True},
                {"name": "Key Level", "value": f"${setup['key_level']}", "inline": True},
                {"name": "Entry Trigger (+1.15 ATR)", "value": f"${setup['trigger_price']}", "inline": True},
                {"name": "Invalidation", "value": f"${setup['invalidation_price']}", "inline": True},
                {"name": "Vol Rank (IVR)", "value": f"{setup['vol_rank']}% ({setup['vol_regime']})", "inline": True},
                {"name": "Execution Thesis", "value": setup['reasoning'], "inline": False}
            ]
        }]
    }
    requests.post(DISCORD_WEBHOOK, json=content)

def main():
    state = load_state()
    
    spy = yf.download("SPY", period="5d", interval="1d", progress=False)['Close'].pct_change().iloc[-1]
    vix = yf.download("^VIX", period="5d", interval="1d", progress=False)['Close'].iloc[-1]
    
    macro_context = f"SPY 1-Day Change: {spy * 100:.2f}% | VIX Index Level: {vix:.2f}"
    data = [get_market_data(t) for t in WATCHLIST]
    
    prompt = f"""
    Context: {macro_context}
    Market Data: {json.dumps(data, default=str)}
    Evaluate setups using the 3-Layer Integrated Framework (Structure, Confirmation, Volatility).
    Return a valid JSON array of qualified setups.
    """

    system_instruction = """
    You are an expert Quantitative Trader. Filter strictly for high-probability setups.
    Output valid JSON array of objects with keys:
    ticker, direction, setup_type, key_level, trigger_price, invalidation_price, vol_rank, vol_regime, reasoning.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json"
        )
    )

    try:
        setups = json.loads(response.text)
        new_alert_dispatched = False
        
        for setup in setups:
            # Create a unique fingerprint based on ticker, setup type, and price level
            alert_id = f"{setup['ticker']}_{setup['setup_type']}_{setup['key_level']}".replace(" ", "_")
            
            # Check if this exact setup was already alerted in the last 24h
            if alert_id in state:
                continue

            send_alert(setup)
            state[alert_id] = time.time()
            new_alert_dispatched = True

        if new_alert_dispatched:
            save_state(state)
            
    except Exception as e:
        print(f"Error processing scan results: {e}")

if __name__ == "__main__":
    main()