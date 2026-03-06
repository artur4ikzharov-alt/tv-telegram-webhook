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
SYMBOLS_LIMIT  = 150
ATR_LENGTH     = 10
SENSITIVITY    = 10.0
VOL_MA_LEN     = 20
TP1_PCT = 3.5
TP2_PCT = 5.0
TP3_PCT = 7.0
TP4_PCT = 11.0
SL_PCT  = 8.0
MTF_MIN       = 2
MTF_CACHE_TTL = 300

active_trades = {}
mtf_cache     = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


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
            print(f"  HTTP {r.status_code} attempt {attempt+1}")
        except Exception as e:
            print(f"  Request error attempt {attempt+1}: {e}")
        time.sleep(1 + attempt)
    return None


def get_top_symbols(limit=150):
    data = safe_get("https://contract.mexc.com/api/v1/contract/ticker")
    if not data:
        print("  ❌ Не вдалось отримати символи")
        return []
    items = data.get("data", [])
    usdt  = [x for x in items if "USDT" in x["symbol"] and "STOCK" not in x["symbol"]]
    result = [x["symbol"] for x in sorted(usdt, key=lambda x: float(x["amount24"]), reverse=True)[:limit]]
    print(f"  ✅ Символів отримано: {len(result)}")
    return result


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
        t, data = mtf_cache[symbol]
        if now - t < MTF_CACHE_TTL:
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
    mtf_score  = bull_count if side == "BUY" else bear_count
    mtf_lines  = "".join([
        f"  {tf}: {'🟢' if v is True else '🔴' if v is False else '❓'}\n"
        for tf, v in mtf_results.items()
    ])
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
        f"📊 MTF {mtf_score}/{valid_count}:\n"
        f"{mtf_lines}"
        f"------------------------\n"
        f"💵 Сума:  {pos_value:.2f} USDT\n"
        f"📊 Монет: {pos_tokens:.4f}"
    )


# ══════════════════════════════════════════
print("=== SMART SIGNAL PRO — TREND TRADER | 15хв MEXC ===")
send_telegram(
    "🚀 Smart Signal Pro (15хв) запущено!\n"
    "🎯 Пресет: Trend Trader (sensitivity=10, ATR=10)\n"
    "Логіка: Smart Trail + AI Classifier ★\n"
    "Reversal Zones + MTF фільтр\n"
    f"Символів: {SYMBOLS_LIMIT}"
)

first_run = True

while True:
    symbols = get_top_symbols(SYMBOLS_LIMIT)
    if not symbols:
        print("  ⚠️  Символи не отримані, чекаю 60с...")
        time.sleep(60)
        continue

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Сканую {len(symbols)} символів...")

    diag_no_data   = 0
    diag_no_signal = 0
    diag_raw       = 0
    debug_done     = False  # детальний лог тільки першого символу

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

            # ── ДЕБАГ першого символу ──
            if first_run and not debug_done:
                debug_done = True
                c0  = df["close"].iloc[-1]
                pc0 = df["close"].iloc[-2]
                t0  = df["trail"].iloc[-1]
                pt0 = df["trail"].iloc[-2]
                print(f"  🔍 DEBUG {symbol}:")
                print(f"     close[-1]={c0:.6f}  close[-2]={pc0:.6f}")
                print(f"     trail[-1]={t0:.6f}  trail[-2]={pt0:.6f}")
                print(f"     c>t={c0>t0} | pc<=t={pc0<=t0} | pc<=pt={pc0<=pt0}")
                print(f"     Колонки df: {list(df.columns)}")
                print(f"     Рядків: {len(df)}")

            c  = df["close"].iloc[-1]
            pc = df["close"].iloc[-2]
            t  = df["trail"].iloc[-1]
            pt = df["trail"].iloc[-2]

            # Сигнал: поточна свічка перетнула trail
            bull_cross = (c > t) and (pc <= t)
            bear_cross = (c < t) and (pc >= t)

            if not (bull_cross or bear_cross):
                diag_no_signal += 1
                continue

            diag_raw += 1
            print(f"  🔔 {symbol} {'BUY' if bull_cross else 'SELL'} | c={c:.4f} trail={t:.4f}")

            if symbol in active_trades:
                tr = active_trades[symbol]
                if tr["side"] == "BUY":
                    if c <= tr["sl"] or c >= tr["tp4"]:
                        del active_trades[symbol]
                else:
                    if c >= tr["sl"] or c <= tr["tp4"]:
                        del active_trades[symbol]

            if symbol in active_trades:
                continue

            last_ph, last_pl = get_reversal_zones(df)
            is_buy = bull_cross
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

    first_run = False
    print(f"  📊 Діаг: немає_даних={diag_no_data} | немає_сигналу={diag_no_signal} | raw={diag_raw}")
    print(f"  📦 MTF кеш: {len(mtf_cache)} | Активних: {len(active_trades)} | 💤 {CHECK_INTERVAL}с...")
    time.sleep(CHECK_INTERVAL)
