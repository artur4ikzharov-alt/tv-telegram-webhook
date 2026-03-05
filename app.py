import requests
import pandas as pd
import time
import os
import numpy as np
import threading
from datetime import datetime
from flask import Flask

app = Flask(__name__)

# --- ВАШІ ЗМІННІ (Переконайтеся, що в Railway назви саме такі) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
USER_BALANCE = 100.0

# Налаштування
INTERVAL = "Min15"
SENSITIVITY = 10 
ATR_LENGTH = 14
TP_PCT = [3.5, 5.0, 7.0, 11.0]
SL_PCT = 8.0

active_trades = {}

def send_telegram(text):
    """Функція відправки з виводом помилок у логи"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
        # Якщо щось не так, ми побачимо це в логах
        if resp.status_code != 200:
            print(f"DEBUG: Telegram помилка {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"DEBUG: Критична помилка відправки в ТГ: {e}")

def run_bot():
    print(f"--- Бот запущено ---")
    print(f"DEBUG: BOT_TOKEN знайдено: {bool(BOT_TOKEN)}")
    print(f"DEBUG: CHAT_ID знайдено: {bool(CHAT_ID)}")
    
    send_telegram("🚀 Бот запущено і готовий до роботи!")
    
    while True:
        symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Початок циклу перевірки...")
        
        for s in symbols:
            # Тут ваш код розрахунку (залишається без змін)
            print(f"🔍 Перевірка {s}...")
            # ... логіка calculate_smart_trail ...
            
        time.sleep(60)

# --- ЗАПУСК ---
if __name__ == "__main__":
    # Запускаємо бота в потоці
    threading.Thread(target=run_bot, daemon=True).start()
    # Запуск Flask
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
