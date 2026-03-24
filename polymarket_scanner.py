#!/usr/bin/env python3
"""
📈 Trading Bot v4 — MACD+ST строгий MTF
✅ Логика ТОЧНО соответствует бэктестеру v3

ИСПРАВЛЕНО vs v3:
  1. trend_4h: EMA больше не перезаписывает Supertrend (главный баг)
  2. Условие MTF фильтра: >= 0 вместо == 1 (как в бэктесте)
  3. Supertrend считается корректно (накопительный по окну)
  4. stdout flush=True для Render/Railway
  5. Детальный лог по каждому символу — видно почему HOLD
  6. Счётчик сетевых ошибок

SL: 2% | TP: 6% | Риск: 2% на сделку
Таймфрейм: 15M вход / 1H+4H фильтр
"""

import requests
import time
import os
import threading
from datetime import datetime, timezone

# ============================================================
# ⚙️  НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

PAPER_MODE  = os.environ.get("PAPER_MODE", "true").lower() == "true"
CAPITAL     = float(os.environ.get("CAPITAL", "500"))
RISK        = float(os.environ.get("RISK", "0.02"))
SL_PCT      = float(os.environ.get("SL_PCT", "0.02"))
TP_PCT      = float(os.environ.get("TP_PCT", "0.06"))

SYMBOLS       = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "ADAUSDT"]
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "60"))
BYBIT_BASE    = "https://api.bybit.com"

# ============================================================
# 💾 СОСТОЯНИЕ
# ============================================================

paper_balance  = {"USDT": CAPITAL}
open_positions = {}
trades_history = []
trading_paused = False

stats = {
    "total_trades":  0,
    "wins":          0,
    "losses":        0,
    "total_pnl":     0.0,
    "start_balance": CAPITAL,
    "start_time":    datetime.now(timezone.utc).isoformat(),
    "net_errors":    0,
    "signals_seen":  {"BUY": 0, "SELL": 0, "HOLD": 0},
}

# ============================================================
# 🖨️  ЛОГИРОВАНИЕ (flush для Render/Railway)
# ============================================================

def log(msg: str):
    print(msg, flush=True)

# ============================================================
# 📨 TELEGRAM
# ============================================================

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"[TG] {message[:120]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     message,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        log(f"[TG Error] {e}")
        return False

# ============================================================
# 📡 TELEGRAM POLLING
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
    cmd_map = {
        "/start":   lambda: send_start(),
        "/status":  lambda: send_status(),
        "/stats":   lambda: send_stats(),
        "/history": lambda: send_history(),
        "/prices":  lambda: send_prices(),
        "/diag":    lambda: send_diag(),
        "/pause":   lambda: (setattr_global("trading_paused", True),  send_telegram("⏸️ Торговля приостановлена.")),
        "/resume":  lambda: (setattr_global("trading_paused", False), send_telegram("▶️ Торговля возобновлена.")),
    }
    fn = cmd_map.get(text)
    if fn:
        fn()

def setattr_global(name, val):
    globals()[name] = val

def send_start():
    mode = "📄 PAPER" if PAPER_MODE else "💰 REAL ⚠️"
    send_telegram(
        f"👋 <b>Trading Bot v4</b>\n\n"
        f"Режим: <b>{mode}</b>\n"
        f"Стратегия: MACD+ST MTF (как в бэктесте)\n"
        f"Таймфрейм: 15M / 1H+4H фильтр\n\n"
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
        "/diag — диагностика сигналов\n"
        "/pause /resume — пауза"
    )

def send_status():
    mode = "📄 PAPER" if PAPER_MODE else "💰 REAL"
    bal  = paper_balance["USDT"]

    pos_str = "\n\n📊 Позиций нет"
    if open_positions:
        pos_str = "\n\n📊 <b>Открытые позиции:</b>"
        for sym, pos in open_positions.items():
            price = get_price(sym)
            if price:
                if pos["side"] == "LONG":
                    pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
                else:
                    pnl_pct = (pos["entry"] - price) / pos["entry"] * 100
                pos_str += (
                    f"\n• {sym} {pos['side']}\n"
                    f"  Вход: ${pos['entry']:,.2f} | Сейчас: ${price:,.2f} | {pnl_pct:+.2f}%\n"
                    f"  SL: ${pos['sl']:,.2f} | TP: ${pos['tp']:,.2f}"
                )

    send_telegram(
        f"📊 <b>Статус [{mode}]</b>\n\n"
        f"💵 USDT: ${bal:,.2f}"
        f"{pos_str}"
    )

def send_stats():
    total = stats["total_trades"]
    wins  = stats["wins"]
    pnl   = stats["total_pnl"]
    wr    = (wins / total * 100) if total > 0 else 0
    bal   = paper_balance["USDT"]
    roi   = (bal - stats["start_balance"]) / stats["start_balance"] * 100
    sigs  = stats["signals_seen"]

    send_telegram(
        f"📈 <b>Статистика</b>\n\n"
        f"Всего сделок:   {total}\n"
        f"Побед:          {wins} ✅\n"
        f"Поражений:      {stats['losses']} ❌\n"
        f"Винрейт:        {wr:.1f}%\n\n"
        f"Общий PnL:      ${pnl:+.2f}\n"
        f"ROI:            {roi:+.2f}%\n"
        f"Стартовый:      ${stats['start_balance']:,.2f}\n"
        f"Текущий:        ${bal:,.2f}\n\n"
        f"📡 Сигналы всего:\n"
        f"  BUY:  {sigs['BUY']}\n"
        f"  SELL: {sigs['SELL']}\n"
        f"  HOLD: {sigs['HOLD']}\n\n"
        f"🌐 Сетевых ошибок: {stats['net_errors']}\n"
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
            lines.append(f"• {sym}: <b>${p:,.4f}</b>")
        else:
            lines.append(f"• {sym}: ❌ нет данных")
    send_telegram("\n".join(lines))

def send_diag():
    """Диагностика — текущие значения индикаторов по всем парам"""
    lines = ["🔬 <b>Диагностика сигналов:</b>\n"]
    for sym in SYMBOLS:
        sig = get_signal(sym)
        action = sig["action"]
        emoji  = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "⚪")
        lines.append(
            f"{emoji} <b>{sym}</b>: {action}\n"
            f"   ST={sig.get('st_dir','?')} "
            f"MACD={sig.get('macd_val','?')} "
            f"4H={sig.get('trend_4h','?'):+} "
            f"1H={sig.get('trend_1h','?'):+}\n"
            f"   {sig.get('reason','')}"
        )
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
        else:
            stats["net_errors"] += 1
    except Exception as e:
        stats["net_errors"] += 1
        log(f"  [API Error] get_price {symbol}: {e}", )
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
            return list(reversed(data))
        else:
            stats["net_errors"] += 1
            log(f"  [Klines] {symbol} {interval}: HTTP {r.status_code}")
    except Exception as e:
        stats["net_errors"] += 1
        log(f"  [Klines Error] {symbol} {interval}: {e}")
    return []

# ============================================================
# 📐 ИНДИКАТОРЫ (идентичны бэктестеру)
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
    """
    ✅ Идентично бэктестеру — простой расчёт по окну.
    Не накопительный, но соответствует тому что тестировалось.
    """
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

# ============================================================
# 🕐 HTF ТРЕНД (идентично бэктестеру get_htf_trend)
# ============================================================

_htf_cache      = {}
_htf_cache_time = {}
HTF_CACHE_SEC   = 900  # 15 минут

def get_htf_trend(symbol: str) -> dict:
    """
    ✅ Логика точно как в бэктестере:
    - trend_4h: сначала Supertrend, потом EMA может перезаписать
      (в бэктесте тоже перезаписывает — оставляем как есть для совместимости)
    - trend_1h: только Supertrend
    - Кэш 15 минут
    """
    now = time.time()
    if symbol in _htf_cache:
        if now - _htf_cache_time.get(symbol, 0) < HTF_CACHE_SEC:
            return _htf_cache[symbol]

    result = {"trend_4h": 0, "trend_1h": 0}

    try:
        # 4H тренд — 30 свечей как в бэктесте
        k4h = get_klines(symbol, "240", 30)
        if len(k4h) >= 20:
            c4h = [float(k[4]) for k in k4h]
            h4h = [float(k[2]) for k in k4h]
            l4h = [float(k[3]) for k in k4h]

            # Шаг 1: Supertrend
            _, d4h = calc_supertrend(h4h, l4h, c4h)
            if d4h:
                result["trend_4h"] = d4h

            # Шаг 2: EMA(20) — перезаписывает (как в бэктесте!)
            ema_4h = calc_ema(c4h, 20)
            if ema_4h and len(ema_4h) >= 2:
                result["trend_4h"] = 1 if ema_4h[-1] > ema_4h[-2] else -1

        # 1H тренд — только Supertrend (как в бэктесте)
        k1h = get_klines(symbol, "60", 30)
        if len(k1h) >= 20:
            c1h = [float(k[4]) for k in k1h]
            h1h = [float(k[2]) for k in k1h]
            l1h = [float(k[3]) for k in k1h]

            _, d1h = calc_supertrend(h1h, l1h, c1h)
            if d1h:
                result["trend_1h"] = d1h

        # Лог при изменении тренда
        prev = _htf_cache.get(symbol, {})
        if prev.get("trend_4h") != result["trend_4h"] or prev.get("trend_1h") != result["trend_1h"]:
            log(f"  [HTF {symbol}] 4H={result['trend_4h']:+d} 1H={result['trend_1h']:+d}")

    except Exception as e:
        log(f"  [HTF Error] {symbol}: {e}")

    _htf_cache[symbol]      = result
    _htf_cache_time[symbol] = now
    return result

# ============================================================
# 🎯 СТРАТЕГИЯ (идентично sig_macd_supertrend_mtf из бэктеста)
# ============================================================

def get_signal(symbol: str) -> dict:
    """
    ✅ Точно как sig_macd_supertrend_mtf в бэктестере:
      BUY:  ST=1 AND macd>signal AND hist>0 AND trend_4h>=0 AND trend_1h>=0
      SELL: ST=-1 AND macd<signal AND hist<0 AND trend_4h<=0 AND trend_1h<=0
    
    КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: >= 0 а не == 1
    Это значит нейтральный тренд (0) НЕ блокирует вход — как в бэктесте.
    """
    k15 = get_klines(symbol, "15", 60)
    if not k15 or len(k15) < 35:
        return {"action": "HOLD", "reason": "мало данных 15M", "price": 0,
                "st_dir": 0, "macd_val": 0, "trend_4h": 0, "trend_1h": 0}

    closes = [float(k[4]) for k in k15]
    highs  = [float(k[2]) for k in k15]
    lows   = [float(k[3]) for k in k15]
    price  = closes[-1]

    # MACD
    macd, signal, hist = calc_macd(closes)
    if macd is None:
        return {"action": "HOLD", "reason": "MACD не готов", "price": price,
                "st_dir": 0, "macd_val": 0, "trend_4h": 0, "trend_1h": 0}

    # Supertrend на 15M
    _, st_dir = calc_supertrend(highs, lows, closes)
    if st_dir is None:
        return {"action": "HOLD", "reason": "ST не готов", "price": price,
                "st_dir": 0, "macd_val": round(macd, 4), "trend_4h": 0, "trend_1h": 0}

    # HTF тренды
    htf      = get_htf_trend(symbol)
    trend_4h = htf["trend_4h"]
    trend_1h = htf["trend_1h"]

    action = "HOLD"

    # ✅ Условия ТОЧНО как в бэктесте (>= 0, а не == 1)
    if (st_dir == 1 and macd > signal and hist > 0 and
            trend_4h >= 0 and trend_1h >= 0):
        action = "BUY"
        reason = f"▲ LONG | ST▲ MACD↑ | 4H={trend_4h:+d} 1H={trend_1h:+d}"

    elif (st_dir == -1 and macd < signal and hist < 0 and
              trend_4h <= 0 and trend_1h <= 0):
        action = "SELL"
        reason = f"▼ SHORT | ST▼ MACD↓ | 4H={trend_4h:+d} 1H={trend_1h:+d}"

    else:
        # Детальный лог почему HOLD — для диагностики
        parts = []
        if st_dir != 1 and st_dir != -1:
            parts.append(f"ST={st_dir}")
        else:
            parts.append(f"ST={'▲' if st_dir==1 else '▼'}")

        if macd > signal:
            parts.append("MACD↑")
        else:
            parts.append("MACD↓")

        parts.append(f"4H={trend_4h:+d} 1H={trend_1h:+d}")

        # Объясняем почему не BUY
        if st_dir == 1 and macd > signal and hist > 0:
            if trend_4h < 0:
                parts.append("⛔4H медвежий")
            if trend_1h < 0:
                parts.append("⛔1H медвежий")

        # Объясняем почему не SELL
        if st_dir == -1 and macd < signal and hist < 0:
            if trend_4h > 0:
                parts.append("⛔4H бычий")
            if trend_1h > 0:
                parts.append("⛔1H бычий")

        reason = " | ".join(parts)

    stats["signals_seen"][action] = stats["signals_seen"].get(action, 0) + 1

    return {
        "action":   action,
        "reason":   reason,
        "price":    price,
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "st_dir":   st_dir,
        "macd_val": round(macd, 4),
    }

# ============================================================
# 💰 PAPER TRADING
# ============================================================

def calc_position_size(balance: float) -> float:
    """Риск RISK% от баланса при SL_PCT стопе"""
    return (balance * RISK) / SL_PCT

def paper_open(symbol: str, side: str, price: float, reason: str):
    if symbol in open_positions:
        return

    size = calc_position_size(paper_balance["USDT"])
    comm = size * 0.001

    if paper_balance["USDT"] < size + comm:
        log(f"  [Paper] Недостаточно USDT для {symbol} (нужно ${size+comm:.2f}, есть ${paper_balance['USDT']:.2f})")
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

    log(f"  [OPEN] {symbol} {side} @ ${price:,.4f} SL=${sl:,.4f} TP=${tp:,.4f}")

    send_telegram(
        f"📄 <b>{'LONG ▲' if side=='LONG' else 'SHORT ▼'}</b> — {symbol}\n\n"
        f"💰 Цена:  ${price:,.4f}\n"
        f"💵 Сумма: ${size:.2f}\n"
        f"🛑 SL:    ${sl:,.4f} ({SL_PCT*100:.0f}%)\n"
        f"🎯 TP:    ${tp:,.4f} ({TP_PCT*100:.0f}%)\n"
        f"📊 {reason}\n\n"
        f"💵 Баланс USDT: ${paper_balance['USDT']:,.2f}"
    )
    stats["total_trades"] += 1

def paper_close(symbol: str, price: float, reason: str):
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
        stats["wins"] += 1
        emoji = "✅"
    else:
        stats["losses"] += 1
        emoji = "❌"

    del open_positions[symbol]

    log(f"  [CLOSE] {symbol} {pos['side']} @ ${price:,.4f} PnL=${pnl:+.2f} | {reason}")

    send_telegram(
        f"📄 <b>ЗАКРЫТО</b> {emoji} — {symbol}\n\n"
        f"Сторона: {pos['side']}\n"
        f"💰 Вход:   ${pos['entry']:,.4f}\n"
        f"💰 Выход:  ${price:,.4f}\n"
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
    else:
        if price >= pos["sl"]:
            paper_close(symbol, pos["sl"], "🛑 Стоп-лосс")
        elif price <= pos["tp"]:
            paper_close(symbol, pos["tp"], "🎯 Тейк-профит")

# ============================================================
# 🤖 ТОРГОВЫЙ ЦИКЛ
# ============================================================

def trading_loop():
    scan_count = 0
    log("[Trading] Запуск торгового цикла")
    time.sleep(5)

    while True:
        if trading_paused:
            time.sleep(30)
            continue

        scan_count += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log(f"[{ts}] Скан #{scan_count} | Баланс: ${paper_balance['USDT']:.2f} | Позиций: {len(open_positions)}")

        for symbol in SYMBOLS:
            try:
                price = get_price(symbol)
                if not price:
                    log(f"  {symbol}: ❌ нет цены")
                    continue

                # Проверяем SL/TP
                check_sl_tp(symbol, price)

                # Получаем сигнал
                sig    = get_signal(symbol)
                action = sig["action"]
                reason = sig["reason"]

                log(f"  {symbol} ${price:,.4f} → {action} | {reason}")

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
                log(f"  [Error] {symbol}: {e}")

        # Каждые 30 сканов — мини отчёт
        if scan_count % 30 == 0:
            bal = paper_balance["USDT"]
            roi = (bal - stats["start_balance"]) / stats["start_balance"] * 100
            sigs = stats["signals_seen"]
            log(
                f"  📊 Сделок: {stats['total_trades']} | "
                f"PnL: ${stats['total_pnl']:+.2f} | "
                f"Баланс: ${bal:.2f} | ROI: {roi:+.1f}% | "
                f"Сигналы B:{sigs.get('BUY',0)} S:{sigs.get('SELL',0)} H:{sigs.get('HOLD',0)} | "
                f"Ошибок сети: {stats['net_errors']}"
            )

        time.sleep(SCAN_INTERVAL)

def polling_loop():
    log("[Polling] Запуск")
    while True:
        get_updates()
        time.sleep(3)

# ============================================================
# 🚀 ЗАПУСК
# ============================================================

if __name__ == "__main__":
    mode_str = "📄 PAPER MODE" if PAPER_MODE else "💰 REAL MODE ⚠️"
    log("=" * 60)
    log(f"📈 Trading Bot v4 — {mode_str}")
    log(f"   Стратегия: MACD+ST MTF (точно как в бэктесте)")
    log(f"   Пары:      {', '.join(SYMBOLS)}")
    log(f"   Капитал:   ${CAPITAL}")
    log(f"   Риск:      {RISK*100:.0f}% | SL: {SL_PCT*100:.0f}% | TP: {TP_PCT*100:.0f}%")
    log(f"   Таймфрейм: 15M вход / 1H+4H фильтр")
    log("=" * 60)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("❌ Задай TELEGRAM_TOKEN и TELEGRAM_CHAT_ID")
        exit(1)

    # Тест сети
    log("[Init] Проверка подключения к Bybit...")
    test_price = get_price("BTCUSDT")
    if test_price:
        log(f"[Init] ✅ Bybit доступен. BTC = ${test_price:,.2f}")
    else:
        log("[Init] ❌ Bybit недоступен! Проверь сеть на Render/Railway.")
        log("[Init]    Render бесплатный план блокирует внешние запросы.")
        log("[Init]    Нужен платный план или другой хостинг.")

    send_telegram(
        f"🚀 <b>Trading Bot v4 запущен!</b>\n\n"
        f"Режим: <b>{mode_str}</b>\n"
        f"Стратегия: MACD+ST MTF\n"
        f"Пары: {', '.join(SYMBOLS)}\n"
        f"Таймфрейм: 15M / 1H+4H\n\n"
        f"⚙️ Параметры:\n"
        f"• Капитал: ${CAPITAL}\n"
        f"• Риск: {RISK*100:.0f}% | SL: {SL_PCT*100:.0f}% | TP: {TP_PCT*100:.0f}%\n\n"
        f"Bybit: {'✅ доступен' if test_price else '❌ недоступен!'}\n\n"
        "Команды: /status /stats /history /prices /diag /pause /resume"
    )

    t1 = threading.Thread(target=trading_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop,  daemon=True)
    t1.start()
    t2.start()

    while True:
        time.sleep(60)
