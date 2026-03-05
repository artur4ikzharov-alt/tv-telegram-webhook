import os
import requests
import time
from flask import Flask

app = Flask(__name__)

# Пряма перевірка змінних при старті
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

print(f"DEBUG: Старт додатку. BOT_TOKEN знайдено: {bool(BOT_TOKEN)}")

@app.route('/')
def index():
    return "Bot is running!"

# Запуск бота в самому низу
if __name__ == "__main__":
    print("DEBUG: Запуск бота...")
    # Спробуємо надіслати тестове повідомлення одразу при старті
    if BOT_TOKEN and CHAT_ID:
        try:
            requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&text=Бот запустився!")
            print("DEBUG: Тестове повідомлення надіслано!")
        except Exception as e:
            print(f"DEBUG: Помилка відправки: {e}")
    else:
        print("DEBUG: BOT_TOKEN або CHAT_ID порожні!")
        
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
