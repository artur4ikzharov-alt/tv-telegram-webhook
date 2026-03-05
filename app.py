import requests
import pandas as pd
import time
import os
import numpy as np
import threading
from datetime import datetime
from flask import Flask

# ================= CONFIG =================
app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
USER_BALANCE = 100.0

INTERVAL = "Min15"
SENSITIVITY = 10 
ATR_LENGTH = 14
TP_PCT = [3.5, 5.0, 7.0, 11.0]
SL_PCT = 8.0

active_trades = {}

# ================= FUNCTIONS =================
def get_klines(symbol):
    url = f"https://contract.mexc.com/api/v1/contract/kline/{symbol}?interval={INTERVAL}&limit=100"
    try:
        response = requests.get(url, timeout=10).json()
        if not response.get("success"): return None
        df = pd.DataFrame(response["data"])
        for col in ["close", "high", "low"]:
            df[col] = df[col].astype(float)
        return df
    except: return None

def calculate_smart_trail(df):
    atr = (df["high"] - df["low"]).rolling(ATR_LENGTH).mean()
    nLoss = SENSITIVITY * atr
    trail = [np.nan] * len(df)
    for i in range(1, len(df)):
        src = df["close"].iloc[i]
        prev_t = trail[i-1] if not np.isnan(trail[i-1]) else src
        if src > prev_t and df["close"].iloc[i-1] > prev_t:
            trail[i] = max(prev_t, src - nLoss.iloc[i])
        elif src < prev_t and df["close"].iloc[i-1] < prev_t:
            trail[i] = min(prev_t, src + nLoss.iloc[i])
        else:
            trail[i] = src - nLoss.iloc[i] if src > prev_t else src + nLoss.iloc[i]
    return trail

def send_signal(symbol, side, entry):
    sl = entry * (1 - SL_PCT/100) if side == "BUY" else entry * (1 + SL_PCT/100)
    risk_usd = USER_BALANCE * 0.03
    pos_tokens = risk_usd / (entry * (SL_PCT/100))
    msg = (f"🔥 TREND TRADER SIGNAL #{symbol.replace('_', '')}\n"
           f"SIDE: {side} {'🟢' if side == 'BUY' else '🔴'}\n"
           f"Вхід: {entry:.4f}\n"
           f"🛑 SL: {sl:.4f} ({SL_PCT}%)\n"
           f"💰 Обсяг: {pos_tokens:.4f} монет\n"
           f"🎯 TP1: {entry * (1 + 0.035 if side == 'BUY' else 0.965):.4f}\n"
           f"🚀 TP4: {entry * (1 + 0.11 if side == 'BUY' else 0.89):.4f}")
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg})

def run_bot():
    # Повідомлення при старті
    startup_msg = "🚀 Бот Artur Smart Signal Pro (Trend Trader) успішно запущено!"
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": startup_msg})
    
    print("Бот запущено (15m, Trend Trader)")
    while True:
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Починаю сканування {len(symbols)} символів...")
        
        for s in symbols:
            print(f"🔍 Перевіряю: {s}...")
            df = get_klines(s)
            if df is None: continue
            
            df["trail"] = calculate_smart_trail(df)
            curr_c, prev_c = df["close"].iloc[-1], df["close"].iloc[-2]
            curr_t, prev_t = df["trail"].iloc[-1], df["trail"].iloc[-2]
            
            # Логіка сигналів та захист від дублікатів
            if prev_c < prev_t and curr_c > curr_t and s not in active_trades:
                send_signal(s, "BUY", curr_c)
                active_trades[s] = "BUY"
            elif prev_c > prev_t and curr_c < curr_t and s not in active_trades:
                send_signal(s, "SELL", curr_c)
                active_trades[s] = "SELL"
            elif s in active_trades:
                if (active_trades[s] == "BUY" and curr_c < curr_t) or \
                   (active_trades[s] == "SELL" and curr_c > curr_t):
                    del active_trades[s]
                    print(f"✅ Угода по {s} закрита.")
        
        time.sleep(60)

# ================= FLASK & ENTRY =================
@app.route('/')
def index(): return "Bot is running!"

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
