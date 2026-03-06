import requests
import pandas as pd
import time
from datetime import datetime
import os

# ================= CONFIG =================
TOKEN        = os.getenv("TOKEN")
CHAT_ID      = os.getenv("CHAT_ID")
USER_BALANCE = float(os.getenv("USER_BALANCE", "100.0"))

INTERVAL       = "Min15"
CHECK_INTERVAL = 30
SYMBOLS_LIMIT  = 150

# Smart Trail — Trend Trader пресет
ATR_LENGTH  = 10
SENSITIVITY = 10.0

# AI Classifier
VOL_MA_LEN = 20

# SL/TP у %
TP1_PCT = 3.5
TP2_PCT = 5.0
TP3_PCT = 7.0
TP4_PCT = 11.0
SL_PCT  = 8.0

# MTF
MTF_MIN       = 2
MTF_CACHE_TTL = 300
# ==========================================

active_trades = {}
mtf_cache     = {}


def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"  TG error: {e}")


def get_top_symbols(limit=150):
    try:
        url  = "https://contract.mexc.com/api/v1/contract/ticker"
        data = requests.get(url, timeout=10).json().get("data", [])
        usdt = [x for x in data if "USDT" in x["symbol"] and "STOCK" not in x["symbol"]]
        return [x["symbol"] for x in sorted(usdt, key=lambda x: float(x["amount24"]), reverse=True)[:limit]]
    except Exception as e:
        print(f"  get_top_symbols error: {e}")
        return []


def get_klines(symbol, interval=None, limit=250):
    try:
        iv  = interval or INTERVAL
        url = f"https://contract.mexc.com/api/v1/contract/kline/{symbol}"
        r   = requests.get(url, params={"interval": iv, "limit": limit}, timeout=10).json()
        if not r.get("success"):
            return None
        df = pd.DataFrame(r["data"])
        if df.empty:
            return None
        for c in ["close", "high", "low", "open", "vol"]:
            if c in df.columns:
                df[c] = df[c].astype(float)
        return df.reset_index(drop=True)
    except:
        return None


def calculate_atr(df, period):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def calculate_smart_trail(df, sensitivity, atr_col="atr"):
    n_loss = sensitivity * df[atr_col]
    trail  = [None] * len(df)
    trail[0] = df["close"].iloc[0]

    for i in range(1, len(df)):
        src  = df["close"].iloc[i]
        src1 = df["close"].iloc[i-1]
        nl   = n_loss.iloc[i]
        pt   = trail[i-1] if trail[i-1] is not None else src

        if src > pt and src1 > pt:
            trail[i] = max(pt, src - nl)
        elif src < pt and src1 < pt:
            trail[i] = min(pt, src + nl)
        elif src > pt:
            trail[i] = src - nl
        else:
            trail[i] = src + nl

    return trail


def get_reversal_zones(df, pivot_len=5):
    highs   = df["high"].values
    lows    = df["low"].values
    last_ph = None
    last_pl = None

    for i in range(pivot_len, len(df) - pivot_len):
        wh = highs[i - pivot_len : i + pivot_len + 1]
        wl = lows [i - pivot_len : i + pivot_len + 1]
        if len(wh) == pivot_len * 2 + 1:
            if highs[i] == max(wh):
                last_ph = highs[i]
            if lows[i] == min(wl):
                last_pl = lows[i]

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
    if is_buy and last_pl is not None:
        if abs(close - last_pl) / close < 0.02:
            zone_score = 1
    elif not is_buy and last_ph is not None:
        if abs(close - last_ph) / close < 0.02:
            zone_score = 1

    quality = min(vol_score + atr_score + zone_score + 1, 4)
    stars   = "★" * quality
    return quality, stars


def get_trend_tf(symbol, interval):
    df = get_klines(symbol, interval=interval, limit=60)
    time.sleep(0.15)
    if df is None or len(df) < 15:
        return None
    df["atr"]   = calculate_atr(df, ATR_LENGTH)
    df["trail"] = calculate_smart_trail(df, SENSITIVITY)
    df = df.dropna(subset=["atr"]).reset_index(drop=True)
    if len(df) < 2:
        return None
    return bool(df["close"].iloc[-1] > df["trail"].iloc[-1])


def get_mtf_cached(symbol):
    now = time.time()
    if symbol in mtf_cache:
        cached_time, data = mtf_cache[symbol]
        if now - cached_time < MTF_CACHE_TTL:
            return data

    timeframes = {"1г": "Min60", "4г": "Hour4", "1д": "Day1"}
    results    = {}
    for label, tf in timeframes.items():
        results[label] = get_trend_tf(symbol, tf)

    valid      = {k: v for k, v in results.items() if v is not None}
    bull_count = sum(1 for v in valid.values() if v is True)
    bear_count = sum(1 for v in valid.values() if v is False)

    print(f"  MTF {symbol}: bull={bull_count} bear={bear_count} | " +
          " ".join([f"{k}:{'🟢' if v else '🔴'}" for k, v in valid.items()]))

    data = (bull_count, bear_count, results, len(valid))
    mtf_cache[symbol] = (now, data)
    return data


def format_msg(symbol, side, entry, sl, tp1, tp2, tp3, tp4,
               quality, stars, last_ph, last_pl,
               bull_count, bear_count, mtf_results, valid_count):

    s          = symbol.replace("_", "") + ".P"
    risk_dist  = abs(entry - sl)
    risk_usd   = USER_BALANCE * 0.03
    pos_tokens = risk_usd / risk_dist if risk_dist > 0 else 0
    pos_value  = pos_tokens * entry

    mtf_score = bull_count if side == "BUY" else bear_count
    mtf_lines = ""
    for tf, val in mtf_results.items():
        mtf_lines += f"  {tf}: {'🟢' if val is True else '🔴' if val is False else '❓'}\n"

    rz_high_str = f"{last_ph:.6f}" if last_ph else "—"
    rz_low_str  = f"{last_pl:.6f}" if last_pl  else "—"

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
        f"  Опір: {rz_high_str}\n"
        f"  Підтримка: {rz_low_str}\n"
        f"------------------------\n"
        f"📊 MTF {mtf_score}/{valid_count}:\n"
        f"{mtf_lines}"
        f"------------------------\n"
        f"💵 Сума:  {pos_value:.2f} USDT\n"
        f"📊 Монет: {pos_tokens:.4f}"
    )


# ──────────────────────────────────────────
# ГОЛОВНИЙ ЦИКЛ
# ──────────────────────────────────────────
print("=== SMART SIGNAL PRO — TREND TRADER | 15хв MEXC ===")
send_telegram(
    "🚀 Smart Signal Pro (15хв) запущено!\n"
    "🎯 Пресет: Trend Trader (sensitivity=10, ATR=10)\n"
    f"Логіка: Smart Trail + AI Classifier ★\n"
    f"Reversal Zones + MTF фільтр\n"
    f"Символів: {SYMBOLS_LIMIT}"
)

while True:
    symbols = get_top_symbols(SYMBOLS_LIMIT)
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Сканую {len(symbols)} символів...")

    diag_no_data   = 0
    diag_no_signal = 0
    diag_raw       = 0

    for symbol in symbols:
        try:
            df = get_klines(symbol)
            time.sleep(0.05)

            if df is None or len(df) < 30:
                diag_no_data += 1
                continue

            df["atr"]   = calculate_atr(df, ATR_LENGTH)
            df["trail"] = calculate_smart_trail(df, SENSITIVITY)
            df = df.dropna(subset=["atr"]).reset_index(drop=True)
            if len(df) < 5:
                diag_no_data += 1
                continue

            c  = df["close"].iloc[-1]
            pc = df["close"].iloc[-2]
            t  = df["trail"].iloc[-1]
            pt = df["trail"].iloc[-2]

            buy_signal  = (c > t) and (pc <= pt)
            sell_signal = (c < t) and (pc >= pt)

            if not (buy_signal or sell_signal):
                diag_no_signal += 1
                continue

            diag_raw += 1
            print(f"  🔔 {symbol} {'BUY' if buy_signal else 'SELL'} | c={c:.4f} trail={t:.4f}")

            # Перевірка активної угоди
            if symbol in active_trades:
                tr = active_trades[symbol]
                if tr["side"] == "BUY":
                    if c <= tr["sl"] or c >= tr["tp4"]:
                        del active_trades[symbol]
                else:
                    if c >= tr["sl"] or c <= tr["tp4"]:
                        del active_trades[symbol]

            if symbol in active_trades:
                print(f"  ⏭  {symbol}: вже в угоді")
                continue

            last_ph, last_pl = get_reversal_zones(df)
            is_buy = buy_signal
            quality, stars = ai_classifier(df, is_buy, last_ph, last_pl)

            bull_count, bear_count, mtf_results, valid_count = get_mtf_cached(symbol)

            if valid_count < 2:
                print(f"  ⏭  {symbol}: MTF {valid_count}/3 даних, пропуск")
                continue

            mtf_ok = (bull_count >= MTF_MIN) if is_buy else (bear_count >= MTF_MIN)
            if not mtf_ok:
                print(f"  ⏭  {symbol}: MTF не підтверджує | bull={bull_count} bear={bear_count}")
                continue

            side = "BUY" if is_buy else "SELL"
            mult = 1 if is_buy else -1

            sl  = c * (1 - mult * SL_PCT  / 100)
            tp1 = c * (1 + mult * TP1_PCT / 100)
            tp2 = c * (1 + mult * TP2_PCT / 100)
            tp3 = c * (1 + mult * TP3_PCT / 100)
            tp4 = c * (1 + mult * TP4_PCT / 100)

            msg = format_msg(
                symbol, side, c, sl, tp1, tp2, tp3, tp4,
                quality, stars, last_ph, last_pl,
                bull_count, bear_count, mtf_results, valid_count
            )
            send_telegram(msg)
            print(f"  ✅ СИГНАЛ: {symbol} {side} | {stars}")

            active_trades[symbol] = {"side": side, "sl": sl, "tp4": tp4}

        except Exception as e:
            print(f"  ❌ {symbol}: {e}")
            continue

    print(f"  📊 Діаг: немає_даних={diag_no_data} | немає_сигналу={diag_no_signal} | raw={diag_raw}")
    print(f"  📦 MTF кеш: {len(mtf_cache)} | Активних: {len(active_trades)} | 💤 {CHECK_INTERVAL}с...")
    time.sleep(CHECK_INTERVAL)
