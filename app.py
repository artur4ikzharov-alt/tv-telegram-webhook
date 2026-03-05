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
# Отримуємо змінні
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
    except Exception as e:
        print(f"DEBUG: Error in get_klines for {symbol}: {e}")
        return None

def send_signal(symbol, side, entry):
    try:
        sl = entry * (1 - SL_PCT/100) if side == "BUY" else entry * (1 + SL_PCT/100)
        risk_usd = USER_BALANCE * 0.03
        pos_tokens = risk_usd / (entry * (SL_PCT/100))
        msg = (f"🔥 TREND TRADER SIGNAL #{symbol.replace('_', '')}\n"
               f"SIDE: {side} {'🟢' if side == 'BUY' else '🔴'}\n"
               f"Вхід: {entry:.4f}\n"
               f"🛑 SL: {sl:.4f}\n"
               f"💰 Обсяг: {pos_tokens:.4f} монет")
        
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
        print(f"DEBUG: Telegram API response: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"DEBUG: Error in send_signal: {e}")

def run_bot():
    print("--- Бот запускається ---")
    print(f"DEBUG: BOT_TOKEN present: {bool(BOT_TOKEN)}")
    print(f"DEBUG: CHAT_ID present: {bool(CHAT_ID)}")
    
    # Спроба відправити стартове повідомлення
    try:
        startup_msg = "🚀 Бот Artur Smart Signal Pro запущено!"
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                             json={"chat_id": CHAT_ID, "text": startup_msg})
        print(f"DEBUG: Startup message response: {resp.status_code}")
    except Exception as e:
        print(f"DEBUG: Startup message failed: {e}")
    
    while True:
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Сканування...")
        
        for s in symbols:
            df = get_klines(s)
            if df is None: continue
            
            # (Логіка сигналів без змін)
            # ... 
        
        time.sleep(60)

# ================= FLASK & ENTRY =================
@app.route('/')
def index(): return "Bot is running!"

if __name__ == "__main__":
    # Запускаємо в потоці
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
