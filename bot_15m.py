import requests
import pandas as pd
import time
from datetime import datetime
import os

TOKEN        = os.getenv("TOKEN")
CHAT_ID      = os.getenv("CHAT_ID")
USER_BALANCE = float(os.getenv("USER_BALANCE", "100.0"))

INTERVAL       = "Min15"
CHECK_INTERVAL = 30
ATR_LENGTH     = 10
SENSITIVITY    = 10.0
VOL_MA_LEN     = 20
TP1_PCT = 3.5
TP2_PCT = 5.0
TP3_PCT = 7.0
TP4_PCT = 11.0
SL_PCT  = 8.5

active_trades = {}
signal_cache  = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://futures.mexc.com/",
    "Origin": "https://futures.mexc.com",
}

WATCHED_SYMBOLS = [
    "ORDI_USDT", "AAVE_USDT", "ARB_USDT",  "DOT_USDT",  "LINK_USDT",
    "BTC_USDT",  "ETH_USDT",  "SOL_USDT",  "XRP_USDT",  "ZEC_USDT",
    "PEPE_USDT", "WIF_USDT",  "LDO_USDT",  "XAUT_USDT", "UNI_USDT",
    "AXS_USDT",  "DYDX_USDT", "LTC_USDT",  "HYPE_USDT", "NEAR_USDT"
]


def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"  TG error: {e}")


def safe_get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 200 and r.text.strip():
                return r.json()
            if r.status_code == 403:
                print(f"  HTTP 403 — чекаю 5с attempt {attempt+1}")
                time.sleep(5)
            else:
                print(f"  HTTP {r.status_code} attempt {attempt+1}")
        except Exception as e:
            print(f"  Request error attempt {attempt+1}: {e}")
        time.sleep(2 + attempt * 2)
    return None


def get_klines(symbol, interval=None, limit=250):
    iv  = interval or INTERVAL
    url = f"https://contract.mexc.com/api/v1/contract/kline/{symbol}"
    r   = safe_get(url, params={"interval": iv, "limit": limit})
    if not r or r.get("success") not in (True, 1):
        return None
    df = pd.DataFrame(r["data"])
    if df.empty:
        return None
    for c in ["close", "high", "low", "open", "vol"]:
        if c in df.columns:
            df[c] = df[c].astype(float)
    return df.reset_index(drop=True)


def calculate_atr(df, period):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def calculate_smart_trail(df, sensitivity):
    n_loss = sensitivity * df["atr"]
    trail  = [float("nan")] * len(df)
    trail[0] = df["close"].iloc[0]
    for i in range(1, len(df)):
        src  = df["close"].iloc[i]
        src1 = df["close"].iloc[i-1]
        nl   = n_loss.iloc[i]
        pt   = trail[i-1]
        if src > pt and src1 > pt:
            trail[i] = max(pt, src - nl)
        elif src < pt and src1 < pt:
            trail[i] = min(pt, src + nl)
        elif src > pt:
            trail[i] = src - nl
        else:
            trail[i] = src + nl
    return trail


def find_crossover(df):
    n = len(df)
    for i in [n - 2, n - 3, n - 4, n - 5, n - 6]:
        if i < 1:
            continue
        c  = df["close"].iloc[i]
        pc = df["close"].iloc[i - 1]
        t  = df["trail"].iloc[i]
        pt = df["trail"].iloc[i - 1]
        if (c > t) and (pc <= pt):
            return "BUY", i
        if (c < t) and (pc >= pt):
            return "SELL", i
    return None, None


def get_reversal_zones(df, pivot_len=5):
    highs = df["high"].values
    lows  = df["low"].values
    last_ph = None
    last_pl = None
    for i in range(pivot_len, len(df) - pivot_len):
        wh = highs[i - pivot_len : i + pivot_len + 1]
        wl = lows [i - pivot_len : i + pivot_len + 1]
        if len(wh) == pivot_len * 2 + 1:
            if highs[i] == max(wh): last_ph = highs[i]
            if lows[i]  == min(wl): last_pl = lows[i]
    return last_ph, last_pl


def ai_classifier(df, is_buy, last_ph, last_pl):
    close  = df["close"].iloc[-1]
    volume = df["vol"].iloc[-1] if "vol" in df.columns else 0
    vol_ma = df["vol"].rolling(VOL_MA_LEN).mean().iloc[-1] if "vol" in df.columns else 1
    atr    = df["atr"].iloc[-1]
    atr_ma = df["atr"].rolling(20).mean().iloc[-1]
    vol_score  = 2 if volume > vol_ma * 2.0 else (1 if volume > vol_ma * 1.5 else 0)
    atr_score  = 1 if atr > atr_ma else 0
    zone_score = 0
    if is_buy and last_pl and abs(close - last_pl) / close < 0.02:
        zone_score = 1
    elif not is_buy and last_ph and abs(close - last_ph) / close < 0.02:
        zone_score = 1
    quality = min(vol_score + atr_score + zone_score + 1, 4)
    return quality, "★" * quality


def format_msg(symbol, side, entry, sl, tp1, tp2, tp3, tp4,
               quality, stars, last_ph, last_pl):
    s          = symbol.replace("_", "") + ".P"
    risk_dist  = abs(entry - sl)
    risk_usd   = USER_BALANCE * 0.03
    pos_tokens = risk_usd / risk_dist if risk_dist > 0 else 0
    pos_value  = pos_tokens * entry
    return (
        f"{'🟢' if side == 'BUY' else '🔴'} СИГНАЛ {side} | {stars} ({quality}/4)\n"
        f"#{s} (15хв)\n"
        f"========================\n"
        f"💰 Вхід:      {entry}\n"
        f"🛑 SL  (-{SL_PCT}%): {sl:.6f}\n"
        f"🎯 TP1 (+{TP1_PCT}%): {tp1:.6f}\n"
        f"🎯 TP2 (+{TP2_PCT}%): {tp2:.6f}\n"
        f"🎯 TP3 (+{TP3_PCT}%): {tp3:.6f}\n"
        f"🚀 TP4 (+{TP4_PCT}%): {tp4:.6f}\n"
        f"------------------------\n"
        f"📐 Зони розвороту:\n"
        f"  Опір: {f'{last_ph:.6f}' if last_ph else '—'}\n"
        f"  Підтримка: {f'{last_pl:.6f}' if last_pl else '—'}\n"
        f"------------------------\n"
        f"💵 Сума:  {pos_value:.2f} USDT\n"
        f"📊 Монет: {pos_tokens:.4f}"
    )


# ══════════════════════════════════════════
print("=== SMART SIGNAL PRO — TREND TRADER | 15хв MEXC ===")
send_telegram(
    "🚀 Smart Signal Pro (15хв) запущено!\n"
    "📊 Біржа: MEXC\n"
    "🎯 Пресет: Trend Trader (sensitivity=10, ATR=10)\n"
    "Логіка: Smart Trail crossover + AI ★\n"
    f"Символів: {len(WATCHED_SYMBOLS)}"
)

while True:
    symbols = WATCHED_SYMBOLS
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Сканую {len(symbols)} символів...")

    diag_no_data   = 0
    diag_no_signal = 0
    diag_raw       = 0

    for symbol in symbols:
        try:
            df = get_klines(symbol)
            time.sleep(0.05)

            if df is None:
                diag_no_data += 1
                print(f"  ⚠️  {symbol}: немає даних")
                continue

            if len(df) < 30:
                diag_no_data += 1
                continue

            df["atr"]   = calculate_atr(df, ATR_LENGTH)
            df["trail"] = pd.array(calculate_smart_trail(df, SENSITIVITY), dtype=float)
            df = df.dropna(subset=["atr", "trail"]).reset_index(drop=True)
            if len(df) < 5:
                diag_no_data += 1
                continue

            side, sig_idx = find_crossover(df)

            if side is None:
                diag_no_signal += 1
                continue

            sig_time  = df["time"].iloc[sig_idx] if "time" in df.columns else sig_idx
            cache_key = f"{symbol}_{sig_time}"
            if cache_key in signal_cache:
                diag_no_signal += 1
                continue
            signal_cache[cache_key] = True

            diag_raw += 1
            c = df["close"].iloc[sig_idx]
            t = df["trail"].iloc[sig_idx]
            print(f"  🔔 {symbol} {side} c={c:.4f} trail={t:.4f}")

            if symbol in active_trades:
                tr  = active_trades[symbol]
                cur = df["close"].iloc[-1]
                if tr["side"] == "BUY":
                    if cur <= tr["sl"] or cur >= tr["tp4"]:
                        del active_trades[symbol]
                else:
                    if cur >= tr["sl"] or cur <= tr["tp4"]:
                        del active_trades[symbol]

            if symbol in active_trades:
                print(f"  ⏭  {symbol}: вже в угоді")
                continue

            last_ph, last_pl = get_reversal_zones(df)
            is_buy = (side == "BUY")
            quality, stars = ai_classifier(df, is_buy, last_ph, last_pl)

            mult = 1 if is_buy else -1
            sl   = c * (1 - mult * SL_PCT  / 100)
            tp1  = c * (1 + mult * TP1_PCT / 100)
            tp2  = c * (1 + mult * TP2_PCT / 100)
            tp3  = c * (1 + mult * TP3_PCT / 100)
            tp4  = c * (1 + mult * TP4_PCT / 100)

            msg = format_msg(
                symbol, side, c, sl, tp1, tp2, tp3, tp4,
                quality, stars, last_ph, last_pl
            )
            send_telegram(msg)
            print(f"  ✅ СИГНАЛ: {symbol} {side} | {stars}")

            active_trades[symbol] = {"side": side, "sl": sl, "tp4": tp4}

        except Exception as e:
            print(f"  ❌ {symbol}: {e}")
            continue

    print(f"  📊 Діаг: немає_даних={diag_no_data} | немає_сигналу={diag_no_signal} | raw={diag_raw}")
    print(f"  📦 Активних: {len(active_trades)} | 💤 {CHECK_INTERVAL}с...")
    time.sleep(CHECK_INTERVAL)
