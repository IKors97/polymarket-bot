#!/usr/bin/env python3
"""
📈 Trading Bot v2 — Multi-Strategy Tester
Автоматически тестирует стратегии по очереди:
1. MACD + Supertrend (лучший комбо)
2. Supertrend (простой и эффективный)
3. VWAP (профессиональный)
4. Bollinger + Volume (боковик)
5. EMA + RSI (базовый)

Каждая стратегия тестируется 3 дня.
Победитель остаётся навсегда.
"""

import requests
import time
import json
import os
import threading
from datetime import datetime, timezone, timedelta
from collections import deque
import math

# ============================================================
# ⚙️  НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
BYBIT_API_KEY     = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET  = os.environ.get("BYBIT_API_SECRET", "")

PAPER_MODE        = os.environ.get("PAPER_MODE", "true").lower() == "true"
CAPITAL           = float(os.environ.get("CAPITAL", "500"))
RISK_PER_TRADE    = float(os.environ.get("RISK_PCT", "0.05"))   # 5% на сделку
STOP_LOSS_PCT     = float(os.environ.get("STOP_LOSS", "0.015")) # 1.5%
TAKE_PROFIT_PCT   = float(os.environ.get("TAKE_PROFIT", "0.03"))# 3%
STRATEGY_DAYS     = int(os.environ.get("STRATEGY_DAYS", "3"))   # дней на стратегию
TIMEFRAME         = "15"  # 15 минут
SYMBOLS           = ["BTCUSDT", "ETHUSDT"]
SCAN_INTERVAL     = 60
BYBIT_BASE        = "https://api.bybit.com"

# ============================================================
# 🏆 МЕНЕДЖЕР СТРАТЕГИЙ
# ============================================================

STRATEGIES = [
    "MACD_SUPERTREND",  # 1. Лучший комбо
    "SUPERTREND",       # 2. Простой и эффективный
    "VWAP",             # 3. Профессиональный
    "BOLLINGER_VOLUME", # 4. Боковик
    "EMA_RSI",          # 5. Базовый
]

STRATEGY_NAMES = {
    "MACD_SUPERTREND":  "MACD + Supertrend",
    "SUPERTREND":       "Supertrend",
    "VWAP":             "VWAP",
    "BOLLINGER_VOLUME": "Bollinger + Volume",
    "EMA_RSI":          "EMA + RSI",
}

# Текущая стратегия
current_strategy_idx = 0
strategy_start_time  = datetime.now(timezone.utc)
best_strategy        = None
best_strategy_pnl    = float("-inf")

# Статистика каждой стратегии
strategy_stats = {s: {
    "trades": 0, "wins": 0, "losses": 0,
    "pnl": 0.0, "start_balance": CAPITAL
} for s in STRATEGIES}

# ============================================================
# 💾 СОСТОЯНИЕ
# ============================================================

paper_balance  = {"USDT": CAPITAL, "BTC": 0.0, "ETH": 0.0}
open_positions = {}
trades_history = []
trading_paused = False

global_stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl": 0.0,
    "start_balance": CAPITAL,
    "start_time": datetime.now(timezone.utc).isoformat(),
}

# ============================================================
# 📨 TELEGRAM
# ============================================================

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TG] {message[:80]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[TG Error] {e}")
        return False

# ============================================================
# 📡 POLLING
# ============================================================

last_update_id = 0

def get_updates():
    global last_update_id
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 5},
            timeout=10
        )
        if r.status_code == 200:
            for upd in r.json().get("result", []):
                last_update_id = upd["update_id"]
                handle_command(upd)
    except Exception:
        pass

def handle_command(update: dict):
    global trading_paused
    text = update.get("message", {}).get("text", "")
    if not text:
        return
    if text == "/start":
        send_start()
    elif text == "/status":
        send_status()
    elif text == "/stats":
        send_stats()
    elif text == "/compare":
        send_compare()
    elif text == "/history":
        send_history()
    elif text == "/prices":
        send_prices()
    elif text == "/pause":
        trading_paused = True
        send_telegram("⏸️ Пауза. /resume чтобы продолжить.")
    elif text == "/resume":
        trading_paused = False
        send_telegram("▶️ Торговля возобновлена.")
    elif text == "/next":
        switch_strategy(forced=True)

def send_start():
    cur = STRATEGIES[current_strategy_idx]
    days_left = STRATEGY_DAYS - (datetime.now(timezone.utc) - strategy_start_time).days
    send_telegram(
        "👋 <b>Multi-Strategy Trading Bot v2</b>\n\n"
        f"Режим: {'📄 PAPER' if PAPER_MODE else '💰 REAL'}\n"
        f"Пары: BTC/USDT + ETH/USDT\n"
        f"Таймфрейм: {TIMEFRAME} мин\n"
        f"Капитал: ${CAPITAL}\n\n"
        f"🔄 Тест стратегий:\n"
        + "\n".join([
            f"{'▶️' if i == current_strategy_idx else '⏳'} "
            f"{i+1}. {STRATEGY_NAMES[s]}"
            for i, s in enumerate(STRATEGIES)
        ]) +
        f"\n\nТекущая: <b>{STRATEGY_NAMES[cur]}</b>\n"
        f"Осталось дней: {max(days_left, 0)}\n\n"
        "Команды:\n"
        "/status — баланс\n"
        "/stats — общая статистика\n"
        "/compare — сравнение стратегий\n"
        "/history — сделки\n"
        "/prices — цены\n"
        "/next — следующая стратегия\n"
        "/pause /resume"
    )

def send_status():
    cur  = STRATEGIES[current_strategy_idx]
    s    = strategy_stats[cur]
    wr   = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
    bal  = paper_balance["USDT"]
    days_elapsed = (datetime.now(timezone.utc) - strategy_start_time).days
    days_left    = max(STRATEGY_DAYS - days_elapsed, 0)

    pos_str = ""
    if open_positions:
        pos_str = "\n\n📊 <b>Позиции:</b>"
        for sym, pos in open_positions.items():
            price = get_price(sym)
            if price:
                pnl = (price - pos["entry"]) / pos["entry"] * 100
                pos_str += f"\n• {sym}: ${pos['entry']:,.0f}→${price:,.0f} ({pnl:+.1f}%)"
    else:
        pos_str = "\n\n📊 Позиций нет"

    send_telegram(
        f"📊 <b>Статус</b>\n\n"
        f"Стратегия: <b>{STRATEGY_NAMES[cur]}</b>\n"
        f"День {days_elapsed+1}/{STRATEGY_DAYS} (осталось {days_left} дн.)\n\n"
        f"💵 Баланс USDT: ${bal:,.2f}\n"
        f"₿ BTC: {paper_balance['BTC']:.6f}\n"
        f"Ξ ETH: {paper_balance['ETH']:.4f}\n\n"
        f"Сделок: {s['trades']} | Побед: {s['wins']} | Вин%: {wr:.0f}%\n"
        f"PnL стратегии: ${s['pnl']:+.2f}"
        f"{pos_str}"
    )

def send_stats():
    g   = global_stats
    wr  = (g["wins"] / g["total_trades"] * 100) if g["total_trades"] > 0 else 0
    roi = ((paper_balance["USDT"] - g["start_balance"]) / g["start_balance"] * 100)
    send_telegram(
        f"📈 <b>Общая статистика</b>\n\n"
        f"Всего сделок: {g['total_trades']}\n"
        f"Побед: {g['wins']} ✅ | Поражений: {g['losses']} ❌\n"
        f"Винрейт: {wr:.1f}%\n\n"
        f"Стартовый баланс: ${g['start_balance']:,.2f}\n"
        f"Текущий баланс:   ${paper_balance['USDT']:,.2f}\n"
        f"Общий PnL: ${g['total_pnl']:+.2f}\n"
        f"ROI: {roi:+.2f}%\n\n"
        f"Старт: {g['start_time'][:10]}"
    )

def send_compare():
    """Сравниваем все протестированные стратегии"""
    lines = ["🏆 <b>Сравнение стратегий:</b>\n"]
    results = []

    for i, s in enumerate(STRATEGIES):
        st  = strategy_stats[s]
        is_current = (i == current_strategy_idx)
        tested     = st["trades"] > 0

        if not tested and not is_current:
            results.append((s, None))
            continue

        wr = (st["wins"] / st["trades"] * 100) if st["trades"] > 0 else 0
        results.append((s, {"pnl": st["pnl"], "wr": wr, "trades": st["trades"]}))

    # Сортируем по PnL
    tested   = [(s, r) for s, r in results if r is not None]
    untested = [(s, r) for s, r in results if r is None]
    tested.sort(key=lambda x: x[1]["pnl"], reverse=True)

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    rank   = 0

    for s, r in tested:
        cur_marker = " ◀️" if s == STRATEGIES[current_strategy_idx] else ""
        medal      = medals[rank] if rank < len(medals) else "•"
        rank      += 1
        lines.append(
            f"{medal} <b>{STRATEGY_NAMES[s]}</b>{cur_marker}\n"
            f"   PnL: ${r['pnl']:+.2f} | "
            f"Вин%: {r['wr']:.0f}% | "
            f"Сделок: {r['trades']}"
        )

    for s, _ in untested:
        lines.append(f"⏳ {STRATEGY_NAMES[s]} — не протестирована")

    if best_strategy:
        lines.append(f"\n🏆 Лидер: <b>{STRATEGY_NAMES[best_strategy]}</b>")

    send_telegram("\n".join(lines))

def send_history():
    if not trades_history:
        send_telegram("📭 Сделок ещё не было.")
        return
    lines = ["📋 <b>Последние сделки:</b>\n"]
    for t in trades_history[-8:][::-1]:
        emoji = "✅" if t["pnl"] > 0 else "❌"
        lines.append(
            f"{emoji} {t['symbol']} [{t['strategy']}]\n"
            f"   ${t['entry']:,.0f}→${t['exit']:,.0f} | "
            f"PnL: ${t['pnl']:+.2f}\n"
            f"   {t['reason']} | {t['time'][11:16]}"
        )
    send_telegram("\n".join(lines))

def send_prices():
    lines = ["💹 <b>Цены:</b>\n"]
    for sym in SYMBOLS:
        p = get_price(sym)
        if p:
            lines.append(f"• {sym}: <b>${p:,.2f}</b>")
    send_telegram("\n".join(lines))

# ============================================================
# 📊 BYBIT API
# ============================================================

def get_price(symbol: str) -> float | None:
    try:
        r = requests.get(
            f"{BYBIT_BASE}/v5/market/tickers",
            params={"category": "spot", "symbol": symbol},
            timeout=8
        )
        if r.status_code == 200:
            items = r.json().get("result", {}).get("list", [])
            if items:
                return float(items[0].get("lastPrice", 0))
    except Exception:
        pass
    return None

def get_klines(symbol: str, limit: int = 60) -> list:
    try:
        r = requests.get(
            f"{BYBIT_BASE}/v5/market/kline",
            params={"category": "spot", "symbol": symbol,
                    "interval": TIMEFRAME, "limit": limit},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json().get("result", {}).get("list", [])
            return list(reversed(data))  # от старых к новым
    except Exception:
        pass
    return []

# ============================================================
# 📐 ИНДИКАТОРЫ
# ============================================================

def calc_ema(prices: list, period: int) -> list:
    if len(prices) < period:
        return []
    k    = 2 / (period + 1)
    ema  = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_rsi(prices: list, period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    gains  = [max(prices[i]-prices[i-1], 0) for i in range(1, len(prices))]
    losses = [max(prices[i-1]-prices[i], 0) for i in range(1, len(prices))]
    ag     = sum(gains[-period:]) / period
    al     = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_macd(prices: list) -> tuple:
    """Возвращает (macd_line, signal_line, histogram)"""
    if len(prices) < 35:
        return None, None, None
    ema12   = calc_ema(prices, 12)
    ema26   = calc_ema(prices, 26)
    min_len = min(len(ema12), len(ema26))
    if min_len < 9:
        return None, None, None
    macd_line = [ema12[-(min_len-i)] - ema26[-(min_len-i)]
                 for i in range(min_len)]
    signal    = calc_ema(macd_line, 9)
    if not signal:
        return None, None, None
    hist = macd_line[-1] - signal[-1]
    return macd_line[-1], signal[-1], hist

def calc_supertrend(highs, lows, closes, period=10, multiplier=3.0) -> tuple:
    """
    Supertrend индикатор.
    Возвращает (значение, направление) где направление 1=бычий -1=медвежий
    """
    if len(closes) < period + 1:
        return None, None

    # ATR
    trs  = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)

    if len(trs) < period:
        return None, None

    atr = sum(trs[-period:]) / period

    # Базовые полосы
    hl2         = (highs[-1] + lows[-1]) / 2
    upper_band  = hl2 + multiplier * atr
    lower_band  = hl2 - multiplier * atr
    close       = closes[-1]
    prev_close  = closes[-2]

    # Направление
    if close > upper_band:
        direction = 1   # бычий тренд
        value     = lower_band
    elif close < lower_band:
        direction = -1  # медвежий тренд
        value     = upper_band
    else:
        # Продолжаем предыдущий тренд
        if prev_close > hl2:
            direction = 1
            value     = lower_band
        else:
            direction = -1
            value     = upper_band

    return value, direction

def calc_bollinger(prices: list, period=20, std_dev=2.0) -> tuple:
    """Возвращает (upper, middle, lower)"""
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    mid    = sum(window) / period
    std    = (sum((p - mid)**2 for p in window) / period) ** 0.5
    return mid + std_dev * std, mid, mid - std_dev * std

def calc_vwap(highs, lows, closes, volumes) -> float | None:
    """Volume Weighted Average Price"""
    if len(closes) < 5:
        return None
    typical_prices = [(h+l+c)/3 for h, l, c in zip(highs, lows, closes)]
    tp_vol = sum(tp * v for tp, v in zip(typical_prices, volumes))
    total_vol = sum(volumes)
    if total_vol == 0:
        return None
    return tp_vol / total_vol

# ============================================================
# 🎯 СТРАТЕГИИ — СИГНАЛЫ
# ============================================================

def signal_macd_supertrend(klines: list) -> dict:
    """Стратегия 1: MACD + Supertrend — лучший комбо"""
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]

    macd, signal, hist = calc_macd(closes)
    st_val, st_dir     = calc_supertrend(highs, lows, closes)

    if not all([macd, signal, st_dir]):
        return {"action": "HOLD", "reason": "недостаточно данных"}

    action = "HOLD"
    # BUY: Supertrend бычий + MACD выше сигнала
    if st_dir == 1 and macd > signal and hist > 0:
        action = "BUY"
    # SELL: Supertrend медвежий + MACD ниже сигнала
    elif st_dir == -1 and macd < signal and hist < 0:
        action = "SELL"

    return {
        "action": action,
        "reason": f"ST={'▲' if st_dir==1 else '▼'} MACD={macd:.1f} Sig={signal:.1f}",
        "price":  closes[-1],
    }

def signal_supertrend(klines: list) -> dict:
    """Стратегия 2: Только Supertrend"""
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]

    st_val, st_dir     = calc_supertrend(highs, lows, closes)
    prev_val, prev_dir = calc_supertrend(highs[:-1], lows[:-1], closes[:-1])

    if st_dir is None:
        return {"action": "HOLD", "reason": "недостаточно данных"}

    action = "HOLD"
    # BUY: смена тренда на бычий
    if st_dir == 1 and prev_dir == -1:
        action = "BUY"
    # SELL: смена тренда на медвежий
    elif st_dir == -1 and prev_dir == 1:
        action = "SELL"

    return {
        "action": action,
        "reason": f"Supertrend {'▲ бычий' if st_dir==1 else '▼ медвежий'} (смена: {action!='HOLD'})",
        "price":  closes[-1],
    }

def signal_vwap(klines: list) -> dict:
    """Стратегия 3: VWAP"""
    closes  = [float(k[4]) for k in klines]
    highs   = [float(k[2]) for k in klines]
    lows    = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    vwap = calc_vwap(highs, lows, closes, volumes)
    rsi  = calc_rsi(closes)

    if not vwap or not rsi:
        return {"action": "HOLD", "reason": "недостаточно данных"}

    price  = closes[-1]
    action = "HOLD"

    # BUY: цена ниже VWAP + RSI не перепродан слишком сильно
    if price < vwap * 0.999 and rsi < 45:
        action = "BUY"
    # SELL: цена выше VWAP + RSI перекуплен
    elif price > vwap * 1.001 and rsi > 55:
        action = "SELL"

    return {
        "action": action,
        "reason": f"VWAP=${vwap:,.0f} Цена=${price:,.0f} RSI={rsi:.0f}",
        "price":  price,
    }

def signal_bollinger_volume(klines: list) -> dict:
    """Стратегия 4: Bollinger Bands + Volume"""
    closes  = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    upper, mid, lower = calc_bollinger(closes)
    if not upper:
        return {"action": "HOLD", "reason": "недостаточно данных"}

    price      = closes[-1]
    avg_volume = sum(volumes[-20:]) / 20
    cur_volume = volumes[-1]
    vol_ratio  = cur_volume / avg_volume if avg_volume > 0 else 1

    action = "HOLD"
    # BUY: цена у нижней полосы + объём выше среднего
    if price <= lower * 1.001 and vol_ratio > 1.2:
        action = "BUY"
    # SELL: цена у верхней полосы + объём выше среднего
    elif price >= upper * 0.999 and vol_ratio > 1.2:
        action = "SELL"

    return {
        "action": action,
        "reason": f"BB upper={upper:,.0f} lower={lower:,.0f} vol={vol_ratio:.1f}x",
        "price":  price,
    }

def signal_ema_rsi(klines: list) -> dict:
    """Стратегия 5: EMA + RSI (базовый)"""
    closes = [float(k[4]) for k in klines]

    ema_fast = calc_ema(closes, 9)
    ema_slow = calc_ema(closes, 21)
    rsi      = calc_rsi(closes)

    if not ema_fast or not ema_slow or not rsi:
        return {"action": "HOLD", "reason": "недостаточно данных"}

    action = "HOLD"
    ef, es = ema_fast[-1], ema_slow[-1]
    ef_p, es_p = ema_fast[-2], ema_slow[-2]

    if ef_p < es_p and ef > es and rsi < 65:
        action = "BUY"
    elif ef_p > es_p and ef < es and rsi > 35:
        action = "SELL"

    return {
        "action": action,
        "reason": f"EMA {ef:,.0f}/{es:,.0f} RSI {rsi:.0f}",
        "price":  closes[-1],
    }

STRATEGY_FUNCTIONS = {
    "MACD_SUPERTREND":  signal_macd_supertrend,
    "SUPERTREND":       signal_supertrend,
    "VWAP":             signal_vwap,
    "BOLLINGER_VOLUME": signal_bollinger_volume,
    "EMA_RSI":          signal_ema_rsi,
}

def get_signal(symbol: str) -> dict:
    klines   = get_klines(symbol, limit=60)
    if not klines or len(klines) < 30:
        return {"action": "HOLD", "reason": "нет данных", "price": 0}
    strategy = STRATEGIES[current_strategy_idx]
    func     = STRATEGY_FUNCTIONS[strategy]
    result   = func(klines)
    result["strategy"] = strategy
    return result

# ============================================================
# 💰 PAPER TRADING
# ============================================================

def paper_buy(symbol: str, price: float, reason: str, strategy: str):
    if symbol in open_positions:
        return
    asset    = symbol.replace("USDT", "")
    usdt_bal = paper_balance["USDT"]
    size_usd = min(usdt_bal * RISK_PER_TRADE * 6, usdt_bal * 0.25)
    if size_usd < 5:
        return
    qty = size_usd / price
    paper_balance["USDT"]  -= size_usd
    paper_balance[asset]   += qty
    open_positions[symbol]  = {
        "entry":       price,
        "qty":         qty,
        "usd_value":   size_usd,
        "stop_loss":   price * (1 - STOP_LOSS_PCT),
        "take_profit": price * (1 + TAKE_PROFIT_PCT),
        "reason":      reason,
        "strategy":    strategy,
        "time":        datetime.now(timezone.utc).isoformat(),
    }
    send_telegram(
        f"📄 <b>BUY</b> — {symbol}\n"
        f"Стратегия: {STRATEGY_NAMES[strategy]}\n\n"
        f"💰 Цена:   ${price:,.2f}\n"
        f"💵 Сумма:  ${size_usd:.2f}\n"
        f"🛑 Стоп:   ${open_positions[symbol]['stop_loss']:,.2f} (-{STOP_LOSS_PCT*100:.1f}%)\n"
        f"🎯 Тейк:   ${open_positions[symbol]['take_profit']:,.2f} (+{TAKE_PROFIT_PCT*100:.1f}%)\n"
        f"📊 {reason}"
    )
    global_stats["total_trades"] += 1
    strategy_stats[strategy]["trades"] += 1

def paper_sell(symbol: str, price: float, reason: str):
    if symbol not in open_positions:
        return
    pos      = open_positions[symbol]
    asset    = symbol.replace("USDT", "")
    strategy = pos["strategy"]
    proceeds = pos["qty"] * price
    pnl      = proceeds - pos["usd_value"]

    paper_balance["USDT"] += proceeds
    paper_balance[asset]   = max(paper_balance[asset] - pos["qty"], 0)

    trades_history.append({
        "symbol":   symbol,
        "strategy": STRATEGY_NAMES[strategy],
        "entry":    pos["entry"],
        "exit":     price,
        "pnl":      round(pnl, 2),
        "reason":   reason,
        "time":     datetime.now(timezone.utc).isoformat(),
    })

    global_stats["total_pnl"] += pnl
    strategy_stats[strategy]["pnl"] += pnl

    if pnl > 0:
        global_stats["wins"] += 1
        strategy_stats[strategy]["wins"] += 1
        emoji = "✅"
    else:
        global_stats["losses"] += 1
        strategy_stats[strategy]["losses"] += 1
        emoji = "❌"

    del open_positions[symbol]

    send_telegram(
        f"📄 <b>SELL</b> {emoji} — {symbol}\n"
        f"Стратегия: {STRATEGY_NAMES[strategy]}\n\n"
        f"💰 Вход:   ${pos['entry']:,.2f}\n"
        f"💰 Выход:  ${price:,.2f}\n"
        f"{'✅' if pnl>0 else '❌'} PnL:    ${pnl:+.2f}\n"
        f"💵 Баланс: ${paper_balance['USDT']:,.2f}\n"
        f"📊 {reason}"
    )

def check_sl_tp(symbol: str, price: float):
    if symbol not in open_positions:
        return
    pos = open_positions[symbol]
    if price <= pos["stop_loss"]:
        paper_sell(symbol, price, "🛑 Стоп-лосс")
    elif price >= pos["take_profit"]:
        paper_sell(symbol, price, "🎯 Тейк-профит")

# ============================================================
# 🔄 СМЕНА СТРАТЕГИИ
# ============================================================

def switch_strategy(forced: bool = False):
    global current_strategy_idx, strategy_start_time, best_strategy, best_strategy_pnl

    old = STRATEGIES[current_strategy_idx]
    old_pnl = strategy_stats[old]["pnl"]

    # Обновляем лучшую стратегию
    if old_pnl > best_strategy_pnl:
        best_strategy_pnl = old_pnl
        best_strategy     = old

    # Закрываем все позиции перед сменой
    for symbol in list(open_positions.keys()):
        price = get_price(symbol)
        if price:
            paper_sell(symbol, price, "🔄 Смена стратегии")

    # Переходим к следующей
    current_strategy_idx = (current_strategy_idx + 1) % len(STRATEGIES)
    strategy_start_time  = datetime.now(timezone.utc)
    new = STRATEGIES[current_strategy_idx]

    old_st  = strategy_stats[old]
    old_wr  = (old_st["wins"] / old_st["trades"] * 100) if old_st["trades"] > 0 else 0

    msg = (
        f"🔄 <b>Смена стратегии{'(принудительно)' if forced else ''}</b>\n\n"
        f"Завершена: <b>{STRATEGY_NAMES[old]}</b>\n"
        f"PnL: ${old_pnl:+.2f} | Вин%: {old_wr:.0f}% | "
        f"Сделок: {old_st['trades']}\n\n"
        f"Начинаем: <b>{STRATEGY_NAMES[new]}</b>\n"
        f"Тест: {STRATEGY_DAYS} дней\n\n"
    )

    # Если протестировали все — объявляем победителя
    if current_strategy_idx == 0 and not forced:
        msg += f"🏆 Лидер пока: <b>{STRATEGY_NAMES[best_strategy]}</b> (${best_strategy_pnl:+.2f})\n"
        msg += "Используй /compare для полного сравнения"

    send_telegram(msg)
    print(f"[Strategy] Переключаемся на {new}")

# ============================================================
# 🤖 ГЛАВНЫЙ ЦИКЛ
# ============================================================

def trading_loop():
    scan_count = 0
    print("[Trading] Запуск")
    time.sleep(10)

    while True:
        if trading_paused:
            time.sleep(30)
            continue

        scan_count += 1
        ts = datetime.now(timezone.utc)

        # Проверяем нужно ли менять стратегию
        days_elapsed = (ts - strategy_start_time).days
        if days_elapsed >= STRATEGY_DAYS:
            switch_strategy()

        print(f"[{ts.strftime('%H:%M:%S')}] Скан #{scan_count} | {STRATEGY_NAMES[STRATEGIES[current_strategy_idx]]}")

        for symbol in SYMBOLS:
            try:
                price = get_price(symbol)
                if not price:
                    continue

                check_sl_tp(symbol, price)
                signal  = get_signal(symbol)
                action  = signal["action"]
                reason  = signal["reason"]
                strategy = STRATEGIES[current_strategy_idx]

                print(f"  {symbol} ${price:,.0f} → {action} | {reason}")

                if PAPER_MODE:
                    if action == "BUY" and symbol not in open_positions:
                        paper_buy(symbol, price, reason, strategy)
                    elif action == "SELL" and symbol in open_positions:
                        paper_sell(symbol, price, reason)

                time.sleep(2)
            except Exception as e:
                print(f"  [Error] {symbol}: {e}")

        # Каждые 30 сканов — мини отчёт в консоль
        if scan_count % 30 == 0:
            s = strategy_stats[STRATEGIES[current_strategy_idx]]
            print(
                f"  📊 {STRATEGY_NAMES[STRATEGIES[current_strategy_idx]]} | "
                f"Сделок: {s['trades']} | PnL: ${s['pnl']:+.2f} | "
                f"Баланс: ${paper_balance['USDT']:.2f}"
            )

        time.sleep(SCAN_INTERVAL)

def polling_loop():
    print("[Polling] Запуск")
    while True:
        get_updates()
        time.sleep(3)

# ============================================================
# 🚀 ЗАПУСК
# ============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("📈 Multi-Strategy Trading Bot v2")
    print(f"   Token:    {'✅' if TELEGRAM_TOKEN else '❌'}")
    print(f"   Режим:    {'PAPER' if PAPER_MODE else 'REAL ⚠️'}")
    print(f"   Капитал:  ${CAPITAL}")
    print(f"   Стратегий: {len(STRATEGIES)} × {STRATEGY_DAYS} дней")
    print("=" * 55)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Задай TELEGRAM_TOKEN и TELEGRAM_CHAT_ID")
        exit(1)

    first = STRATEGY_NAMES[STRATEGIES[0]]
    send_telegram(
        "🚀 <b>Multi-Strategy Bot v2 запущен!</b>\n\n"
        f"Тестируем {len(STRATEGIES)} стратегий по {STRATEGY_DAYS} дня:\n"
        + "\n".join([f"{i+1}. {STRATEGY_NAMES[s]}" for i, s in enumerate(STRATEGIES)]) +
        f"\n\nСтартуем с: <b>{first}</b>\n"
        f"Пары: BTC + ETH | Таймфрейм: {TIMEFRAME}мин\n"
        f"Режим: {'📄 PAPER' if PAPER_MODE else '💰 REAL'}\n\n"
        "Команды:\n"
        "/status /stats /compare /history\n"
        "/prices /next /pause /resume"
    )

    t1 = threading.Thread(target=trading_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop,  daemon=True)
    t1.start()
    t2.start()

    while True:
        time.sleep(60)
