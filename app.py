import requests
import pandas as pd
import time
import os
import numpy as np
import threading
from flask import Flask

# ================= FLASK & CONFIG =================
app = Flask(__name__)

# Використовуємо змінні середовища як ви просили
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
USER_BALANCE = 100.0

# Параметри пресету Trend Trader
INTERVAL = "Min15"
SENSITIVITY = 10 
ATR_LENGTH = 14
TP_PCT = [3.5, 5.0, 7.0, 11.0]
SL_PCT = 8.0

active_trades = {}

# ================= ОСНОВНА ЛОГІКА =================
# ... (функції get_klines, calculate_smart_trail, send_signal залишаються без змін)

def run_bot():
    """Функція для роботи циклу бота в окремому потоці"""
    print("Бот запущено (15m, Trend Trader)")
    while True:
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        for s in symbols:
            df = get_klines(s)
            if df is None: continue
            
            df["trail"] = calculate_smart_trail(df)
            
            curr_c = df["close"].iloc[-1]
            prev_c = df["close"].iloc[-2]
            curr_t = df["trail"].iloc[-1]
            prev_t = df["trail"].iloc[-2]
            
            # Сигнали
            new_side = None
            if prev_c < prev_t and curr_c > curr_t: new_side = "BUY"
            elif prev_c > prev_t and curr_c < curr_t: new_side = "SELL"
                
            if new_side and s not in active_trades:
                send_signal(s, new_side, curr_c)
                active_trades[s] = new_side
            elif s in active_trades:
                if (active_trades[s] == "BUY" and curr_c < curr_t) or \
                   (active_trades[s] == "SELL" and curr_c > curr_t):
                    del active_trades[s]
        
        time.sleep(60)

# ================= FLASK ENDPOINTS =================
@app.route('/')
def index():
    return "Bot is running!"

if __name__ == "__main__":
    # Запускаємо бота в окремому потоці, щоб Flask міг приймати запити
    threading.Thread(target=run_bot, daemon=True).start()
    
    # Запуск Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
