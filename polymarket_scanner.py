#!/usr/bin/env python3
"""
📈 Trading Bot v3 — MACD+ST строгий MTF
Стратегия: MACD + Supertrend + фильтр 4H/1H
SL: 2% | TP: 6% | Риск: 2% на сделку
Таймфрейм: 15M вход / 1H+4H фильтр
Пары: BTC/USDT + ETH/USDT
"""

import requests
import time
import json
import os
import threading
import hmac
import hashlib
from datetime import datetime, timezone
from collections import deque

# ============================================================
# ⚙️  НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BYBIT_API_KEY    = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")

PAPER_MODE  = os.environ.get("PAPER_MODE", "true").lower() == "true"
CAPITAL     = float(os.environ.get("CAPITAL", "500"))
RISK        = float(os.environ.get("RISK", "0.02"))    # 2% риск на сделку
SL_PCT      = float(os.environ.get("SL_PCT", "0.02"))  # 2% стоп-лосс
TP_PCT      = float(os.environ.get("TP_PCT", "0.06"))  # 6% тейк-профит

SYMBOLS       = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "ADAUSDT"]
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "60"))  # каждую минуту
BYBIT_BASE    = "https://api.bybit.com"

# ============================================================
# 💾 СОСТОЯНИЕ
# ============================================================

paper_balance  = {"USDT": CAPITAL, "BTC": 0.0, "ETH": 0.0}
open_positions = {}   # { "BTCUSDT": { side, entry, size, sl, tp, ... } }
trades_history = []
trading_paused = False

stats = {
    "total_trades": 0,
    "wins":         0,
    "losses":       0,
    "total_pnl":    0.0,
    "start_balance": CAPITAL,
    "start_time":   datetime.now(timezone.utc).isoformat(),
}

# ============================================================
# 📨 TELEGRAM
# ============================================================

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[TG] {message[:100]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id":                TELEGRAM_CHAT_ID,
                "text":                   message,
                "parse_mode":             "HTML",
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
        mode = "📄 PAPER" if PAPER_MODE else "💰 REAL ⚠️"
        send_telegram(
            f"👋 <b>Trading Bot v3</b>\n\n"
            f"Режим: <b>{mode}</b>\n"
            f"Стратегия: MACD+ST строгий MTF\n"
            f"Таймфрейм: 15M / 1H+4H фильтр\n"
            f"Пары: BTC + ETH\n\n"
            f"⚙️ Параметры:\n"
            f"• Капитал: ${CAPITAL}\n"
            f"• Риск/сделку: {RISK*100:.0f}%\n"
            f"• Стоп-лосс: {SL_PCT*100:.0f}%\n"
            f"• Тейк-профит: {TP_PCT*100:.0f}%\n\n"
            "Команды:\n"
            "/status — баланс и позиции\n"
            "/stats — статистика\n"
            "/history — последние сделки\n"
            "/prices — текущие цены\n"
            "/pause — пауза\n"
            "/resume — продолжить"
        )
    elif text == "/status":
        send_status()
    elif text == "/stats":
        send_stats()
    elif text == "/history":
        send_history()
    elif text == "/prices":
        send_prices()
    elif text == "/pause":
        trading_paused = True
        send_telegram("⏸️ Торговля приостановлена.")
    elif text == "/resume":
        trading_paused = False
        send_telegram("▶️ Торговля возобновлена.")

def send_status():
    mode = "📄 PAPER" if PAPER_MODE else "💰 REAL"
    bal  = paper_balance["USDT"] if PAPER_MODE else CAPITAL

    pos_str = ""
    if open_positions:
        pos_str = "\n\n📊 <b>Открытые позиции:</b>"
        for sym, pos in open_positions.items():
            price = get_price(sym)
            if price:
                if pos["side"] == "LONG":
                    pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
                else:
                    pnl_pct = (pos["entry"] - price) / pos["entry"] * 100
                pnl_str = f"{pnl_pct:+.2f}%"
                pos_str += (
                    f"\n• {sym} {pos['side']}\n"
                    f"  Вход: ${pos['entry']:,.2f} | Сейчас: ${price:,.2f} | {pnl_str}\n"
                    f"  SL: ${pos['sl']:,.2f} | TP: ${pos['tp']:,.2f}"
                )
    else:
        pos_str = "\n\n📊 Позиций нет"

    send_telegram(
        f"📊 <b>Статус [{mode}]</b>\n\n"
        f"💵 USDT:  ${bal:,.2f}\n"
        f"₿  BTC:  {paper_balance['BTC']:.6f}\n"
        f"Ξ  ETH:  {paper_balance['ETH']:.4f}"
        f"{pos_str}"
    )

def send_stats():
    total = stats["total_trades"]
    wins  = stats["wins"]
    pnl   = stats["total_pnl"]
    wr    = (wins / total * 100) if total > 0 else 0
    bal   = paper_balance["USDT"] if PAPER_MODE else CAPITAL
    roi   = (bal - stats["start_balance"]) / stats["start_balance"] * 100

    send_telegram(
        f"📈 <b>Статистика</b>\n\n"
        f"Всего сделок:  {total}\n"
        f"Побед:         {wins} ✅\n"
        f"Поражений:     {stats['losses']} ❌\n"
        f"Винрейт:       {wr:.1f}%\n\n"
        f"Общий PnL:     ${pnl:+.2f}\n"
        f"ROI:           {roi:+.2f}%\n"
        f"Стартовый:     ${stats['start_balance']:,.2f}\n"
        f"Текущий:       ${bal:,.2f}\n\n"
        f"Старт: {stats['start_time'][:10]}"
    )

def send_history():
    if not trades_history:
        send_telegram("📭 Сделок ещё не было.")
        return
    lines = ["📋 <b>Последние сделки:</b>\n"]
    for t in trades_history[-8:][::-1]:
        emoji = "✅" if t["pnl"] > 0 else "❌"
        lines.append(
            f"{emoji} {t['symbol']} {t['side']}\n"
            f"   ${t['entry']:,.2f}→${t['exit']:,.2f} | "
            f"PnL: ${t['pnl']:+.2f}\n"
            f"   {t['reason']} | {t['time'][11:16]}"
        )
    send_telegram("\n".join(lines))

def send_prices():
    lines = ["💹 <b>Текущие цены:</b>\n"]
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

def get_klines(symbol: str, interval: str, limit: int = 60) -> list:
    try:
        r = requests.get(
            f"{BYBIT_BASE}/v5/market/kline",
            params={
                "category": "spot",
                "symbol":   symbol,
                "interval": interval,
                "limit":    limit,
            },
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
    k   = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def calc_macd(prices: list) -> tuple:
    if len(prices) < 35:
        return None, None, None
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    n     = min(len(ema12), len(ema26))
    if n < 9:
        return None, None, None
    macd_line = [ema12[-(n-i)] - ema26[-(n-i)] for i in range(n)]
    signal    = calc_ema(macd_line, 9)
    if not signal:
        return None, None, None
    hist = macd_line[-1] - signal[-1]
    return macd_line[-1], signal[-1], hist

def calc_supertrend(highs: list, lows: list, closes: list,
                    period: int = 10, mult: float = 3.0) -> tuple:
    if len(closes) < period + 1:
        return None, None
    trs = [max(
        highs[i] - lows[i],
        abs(highs[i] - closes[i-1]),
        abs(lows[i] - closes[i-1])
    ) for i in range(1, len(closes))]
    if len(trs) < period:
        return None, None
    atr   = sum(trs[-period:]) / period
    hl2   = (highs[-1] + lows[-1]) / 2
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    close = closes[-1]
    prev  = closes[-2]
    if close > upper:
        return lower, 1
    elif close < lower:
        return upper, -1
    return (lower, 1) if prev > hl2 else (upper, -1)

def get_htf_trend(symbol: str) -> dict:
    """
    Получаем тренд с 4H и 1H таймфреймов.
    Точная копия логики из рабочего бэктестера v3:
    Supertrend на 4H + EMA(20) перезаписывает результат для стабильности.
    """
    result = {"trend_4h": 0, "trend_1h": 0}
    try:
        # 4H тренд — берём 30 свечей как в бэктестере
        k4h = get_klines(symbol, "240", 30)
        if len(k4h) >= 20:
            c4h = [float(k[4]) for k in k4h]
            h4h = [float(k[2]) for k in k4h]
            l4h = [float(k[3]) for k in k4h]
            # Supertrend
            _, d4h = calc_supertrend(h4h, l4h, c4h)
            if d4h:
                result["trend_4h"] = d4h
            # EMA(20) перезаписывает — даёт стабильный тренд
            ema_4h = calc_ema(c4h, 20)
            if ema_4h and len(ema_4h) >= 2:
                if ema_4h[-1] > ema_4h[-2]:
                    result["trend_4h"] = 1
                elif ema_4h[-1] < ema_4h[-2]:
                    result["trend_4h"] = -1

        # 1H тренд — только Supertrend как в бэктестере
        k1h = get_klines(symbol, "60", 30)
        if len(k1h) >= 20:
            c1h = [float(k[4]) for k in k1h]
            h1h = [float(k[2]) for k in k1h]
            l1h = [float(k[3]) for k in k1h]
            _, d1h = calc_supertrend(h1h, l1h, c1h)
            if d1h:
                result["trend_1h"] = d1h

    except Exception as e:
        print(f"  [HTF Error] {e}")
    return result

# ============================================================
# 🎯 СТРАТЕГИЯ — MACD+ST строгий MTF
# ============================================================

def get_signal(symbol: str) -> dict:
    """
    MACD + Supertrend на 15M
    Фильтр: 4H И 1H должны совпадать с направлением
    """
    # 15M данные для входа
    k15 = get_klines(symbol, "15", 60)
    if not k15 or len(k15) < 35:
        return {"action": "HOLD", "reason": "мало данных", "price": 0}

    closes  = [float(k[4]) for k in k15]
    highs   = [float(k[2]) for k in k15]
    lows    = [float(k[3]) for k in k15]
    price   = closes[-1]

    # MACD
    macd, signal, hist = calc_macd(closes)
    if not all([macd, signal]):
        return {"action": "HOLD", "reason": "MACD не готов", "price": price}

    # Supertrend
    _, st_dir = calc_supertrend(highs, lows, closes)
    if not st_dir:
        return {"action": "HOLD", "reason": "ST не готов", "price": price}

    # Предыдущий MACD для пересечения
    macd_p, sig_p, _ = calc_macd(closes[:-1])

    # HTF тренды
    htf      = get_htf_trend(symbol)
    trend_4h = htf["trend_4h"]
    trend_1h = htf["trend_1h"]

    action = "HOLD"
    reason = f"ST={'▲' if st_dir==1 else '▼'} MACD={macd:.1f} 4H={trend_4h:+d} 1H={trend_1h:+d}"

    # BUY: ST бычий + MACD выше сигнала + оба HTF бычьи
    if (st_dir == 1 and macd > signal and hist > 0 and
            trend_4h == 1 and trend_1h == 1):
        action = "BUY"
        reason = f"▲ LONG | ST бычий | MACD↑ | 4H+1H бычьи"

    # SHORT: ST медвежий + MACD ниже сигнала + оба HTF медвежьи
    elif (st_dir == -1 and macd < signal and hist < 0 and
              trend_4h == -1 and trend_1h == -1):
        action = "SELL"
        reason = f"▼ SHORT | ST медвежий | MACD↓ | 4H+1H медвежьи"

    return {
        "action":   action,
        "reason":   reason,
        "price":    price,
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "st_dir":   st_dir,
        "macd":     macd,
        "signal":   signal,
    }

# ============================================================
# 💰 PAPER TRADING
# ============================================================

def calc_position_size(balance: float) -> float:
    """Размер позиции: при SL теряем ровно RISK% баланса"""
    risk_amount = balance * RISK   # $10 при $500
    return risk_amount / SL_PCT    # $10 / 2% = $500

def paper_open(symbol: str, side: str, price: float, reason: str):
    """Открываем виртуальную позицию"""
    if symbol in open_positions:
        return

    asset    = symbol.replace("USDT", "")
    size     = calc_position_size(paper_balance["USDT"])
    comm     = size * 0.001  # 0.1% комиссия

    if paper_balance["USDT"] < size + comm:
        print(f"  [Paper] Недостаточно USDT для {symbol}")
        return

    paper_balance["USDT"] -= size + comm

    if side == "LONG":
        sl = price * (1 - SL_PCT)
        tp = price * (1 + TP_PCT)
    else:
        sl = price * (1 + SL_PCT)
        tp = price * (1 - TP_PCT)

    open_positions[symbol] = {
        "side":   side,
        "entry":  price,
        "size":   size,
        "sl":     sl,
        "tp":     tp,
        "reason": reason,
        "time":   datetime.now(timezone.utc).isoformat(),
    }

    send_telegram(
        f"📄 <b>{'LONG ▲' if side=='LONG' else 'SHORT ▼'}</b> — {symbol}\n\n"
        f"💰 Цена:  ${price:,.2f}\n"
        f"💵 Сумма: ${size:.2f}\n"
        f"🛑 SL:    ${sl:,.2f} ({SL_PCT*100:.0f}%)\n"
        f"🎯 TP:    ${tp:,.2f} ({TP_PCT*100:.0f}%)\n"
        f"📊 {reason}\n\n"
        f"💵 Баланс USDT: ${paper_balance['USDT']:,.2f}"
    )
    stats["total_trades"] += 1

def paper_close(symbol: str, price: float, reason: str):
    """Закрываем виртуальную позицию"""
    if symbol not in open_positions:
        return

    pos  = open_positions[symbol]
    comm = pos["size"] * 0.001

    if pos["side"] == "LONG":
        pnl = (price - pos["entry"]) / pos["entry"] * pos["size"]
    else:
        pnl = (pos["entry"] - price) / pos["entry"] * pos["size"]
    pnl -= comm

    paper_balance["USDT"] += pos["size"] + pnl

    trades_history.append({
        "symbol": symbol,
        "side":   pos["side"],
        "entry":  pos["entry"],
        "exit":   price,
        "pnl":    round(pnl, 2),
        "reason": reason,
        "time":   datetime.now(timezone.utc).isoformat(),
    })

    stats["total_pnl"] += pnl
    if pnl > 0:
        stats["wins"]   += 1
        emoji = "✅"
    else:
        stats["losses"] += 1
        emoji = "❌"

    del open_positions[symbol]

    send_telegram(
        f"📄 <b>ЗАКРЫТО</b> {emoji} — {symbol}\n\n"
        f"Сторона: {pos['side']}\n"
        f"💰 Вход:   ${pos['entry']:,.2f}\n"
        f"💰 Выход:  ${price:,.2f}\n"
        f"{'✅' if pnl>0 else '❌'} PnL:    ${pnl:+.2f}\n"
        f"💵 Баланс: ${paper_balance['USDT']:,.2f}\n"
        f"📊 {reason}"
    )

# ============================================================
# 🔄 ПРОВЕРКА SL/TP
# ============================================================

def check_sl_tp(symbol: str, price: float):
    if symbol not in open_positions:
        return
    pos = open_positions[symbol]

    if pos["side"] == "LONG":
        if price <= pos["sl"]:
            paper_close(symbol, pos["sl"], "🛑 Стоп-лосс")
        elif price >= pos["tp"]:
            paper_close(symbol, pos["tp"], "🎯 Тейк-профит")
    else:  # SHORT
        if price >= pos["sl"]:
            paper_close(symbol, pos["sl"], "🛑 Стоп-лосс")
        elif price <= pos["tp"]:
            paper_close(symbol, pos["tp"], "🎯 Тейк-профит")

# ============================================================
# 🤖 ТОРГОВЫЙ ЦИКЛ
# ============================================================

def trading_loop():
    scan_count  = 0
    print("[Trading] Запуск")
    time.sleep(10)

    while True:
        if trading_paused:
            time.sleep(30)
            continue

        scan_count += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] Скан #{scan_count}")

        for symbol in SYMBOLS:
            try:
                price = get_price(symbol)
                if not price:
                    continue

                # Проверяем SL/TP
                check_sl_tp(symbol, price)

                # Получаем сигнал
                sig    = get_signal(symbol)
                action = sig["action"]
                reason = sig["reason"]

                print(f"  {symbol} ${price:,.0f} → {action} | {reason}")

                if PAPER_MODE:
                    if action == "BUY" and symbol not in open_positions:
                        paper_open(symbol, "LONG", price, reason)
                    elif action == "SELL" and symbol not in open_positions:
                        paper_open(symbol, "SHORT", price, reason)
                    elif action == "BUY" and symbol in open_positions:
                        if open_positions[symbol]["side"] == "SHORT":
                            paper_close(symbol, price, "Разворот→LONG")
                            paper_open(symbol, "LONG", price, reason)
                    elif action == "SELL" and symbol in open_positions:
                        if open_positions[symbol]["side"] == "LONG":
                            paper_close(symbol, price, "Разворот→SHORT")
                            paper_open(symbol, "SHORT", price, reason)

                time.sleep(2)

            except Exception as e:
                print(f"  [Error] {symbol}: {e}")

        # Каждые 30 сканов — мини отчёт
        if scan_count % 30 == 0:
            bal = paper_balance["USDT"]
            roi = (bal - stats["start_balance"]) / stats["start_balance"] * 100
            print(
                f"  📊 Сделок: {stats['total_trades']} | "
                f"PnL: ${stats['total_pnl']:+.2f} | "
                f"Баланс: ${bal:.2f} | ROI: {roi:+.1f}%"
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
    mode_str = "📄 PAPER MODE" if PAPER_MODE else "💰 REAL MODE ⚠️"
    print("=" * 55)
    print(f"📈 Trading Bot v3 — {mode_str}")
    print(f"   Стратегия: MACD+ST строгий MTF")
    print(f"   Пары:      {', '.join(SYMBOLS)}")
    print(f"   Капитал:   ${CAPITAL}")
    print(f"   Риск:      {RISK*100:.0f}% | SL: {SL_PCT*100:.0f}% | TP: {TP_PCT*100:.0f}%")
    print(f"   Таймфрейм: 15M вход / 1H+4H фильтр")
    print("=" * 55)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Задай TELEGRAM_TOKEN и TELEGRAM_CHAT_ID")
        exit(1)

    send_telegram(
        f"🚀 <b>Trading Bot v3 запущен!</b>\n\n"
        f"Режим: <b>{mode_str}</b>\n"
        f"Стратегия: MACD+ST строгий MTF\n"
        f"Пары: BTC/USDT + ETH/USDT\n"
        f"Таймфрейм: 15M / 1H+4H\n\n"
        f"⚙️ Параметры:\n"
        f"• Капитал: ${CAPITAL}\n"
        f"• Риск: {RISK*100:.0f}% | SL: {SL_PCT*100:.0f}% | TP: {TP_PCT*100:.0f}%\n\n"
        "Команды: /status /stats /history /prices /pause /resume"
    )

    t1 = threading.Thread(target=trading_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop,  daemon=True)
    t1.start()
    t2.start()

    while True:
        time.sleep(60)
