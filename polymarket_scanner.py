#!/usr/bin/env python3
"""
🔍 Polymarket Scanner Bot
Автоматически сканирует рынки и шлёт алерты в Telegram
"""

import requests
import time
import json
import os
import threading
from datetime import datetime, timezone

# ============================================================
# ⚙️  НАСТРОЙКИ (берутся из переменных окружения)
# ============================================================

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MIN_EDGE       = float(os.environ.get("MIN_EDGE", "0.08"))       # 8% минимальный edge
MIN_VOLUME     = float(os.environ.get("MIN_VOLUME", "5000"))     # $5k минимальный объём
SCAN_INTERVAL  = int(os.environ.get("SCAN_INTERVAL", "600"))     # 10 минут
MIN_PRICE      = 0.03
MAX_PRICE      = 0.80

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ============================================================
# 📨 TELEGRAM — отправка сообщений
# ============================================================

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG] Токен или chat_id не заданы!")
        return False
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id":                TELEGRAM_CHAT_ID,
        "text":                   message,
        "parse_mode":             "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TG Error] {e}")
        return False

# ============================================================
# 📡 TELEGRAM — получение обновлений (polling)
# ============================================================

last_update_id = 0

def get_updates():
    global last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {"offset": last_update_id + 1, "timeout": 5}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            updates = r.json().get("result", [])
            for upd in updates:
                last_update_id = upd["update_id"]
                handle_command(upd)
    except Exception:
        pass

def handle_command(update: dict):
    """Обрабатываем команды от пользователя"""
    msg = update.get("message", {})
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id", "")

    if not text or not chat_id:
        return

    if text == "/start":
        send_telegram(
            "👋 <b>Polymarket Scanner Bot</b>\n\n"
            "Я автоматически ищу недооценённые рынки на Polymarket "
            "и присылаю тебе алерты.\n\n"
            f"⚙️ Текущие настройки:\n"
            f"• Минимальный edge: {MIN_EDGE*100:.0f}%\n"
            f"• Минимальный объём: ${MIN_VOLUME:,.0f}\n"
            f"• Интервал сканирования: {SCAN_INTERVAL//60} мин\n\n"
            "✅ Сканер работает в фоне. Жди алертов!"
        )
    elif text == "/status":
        send_telegram(
            "✅ <b>Бот работает</b>\n\n"
            f"🕐 Время: {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
            f"⏱️ Интервал сканирования: {SCAN_INTERVAL//60} мин\n"
            f"📊 Минимальный edge: {MIN_EDGE*100:.0f}%"
        )

# ============================================================
# 📊 ПАРСИНГ РЫНКОВ
# ============================================================

def get_active_markets(limit: int = 150) -> list:
    try:
        params = {
            "active": "true", "closed": "false",
            "archived": "false", "limit": limit,
            "order": "volume24hr", "ascending": "false",
        }
        r = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[Gamma API Error] {e}")
    return []

def get_clob_price(token_id: str) -> float | None:
    try:
        r = requests.get(
            f"{CLOB_API}/price",
            params={"token_id": token_id, "side": "BUY"},
            timeout=10
        )
        if r.status_code == 200:
            return float(r.json().get("price", 0))
    except Exception:
        pass
    return None

def estimate_fair_probability(market: dict) -> float | None:
    try:
        op = market.get("outcomePrices", "[]")
        if isinstance(op, str):
            op = json.loads(op)
        if len(op) >= 2:
            p_yes, p_no = float(op[0]), float(op[1])
            total = p_yes + p_no
            if total > 0:
                return p_yes / total
    except Exception:
        pass
    return None

def analyze_market(market: dict) -> dict | None:
    try:
        volume    = float(market.get("volume", 0) or 0)
        liquidity = float(market.get("liquidity", 0) or 0)
        if volume < MIN_VOLUME or liquidity < 1000:
            return None

        # Достаём token_id
        token_id = None
        ids = market.get("clobTokenIds")
        if isinstance(ids, str):
            ids = json.loads(ids)
        if isinstance(ids, list) and ids:
            token_id = ids[0]
        if not token_id:
            return None

        fair_prob = estimate_fair_probability(market)
        if fair_prob is None:
            return None

        market_price = get_clob_price(token_id)
        if not market_price or not (MIN_PRICE <= market_price <= MAX_PRICE):
            return None

        edge = fair_prob - market_price
        if edge < MIN_EDGE:
            return None

        # Дней до закрытия
        days_left = None
        end_date = market.get("endDate", "")
        if end_date:
            try:
                end_dt    = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(timezone.utc)).days
            except Exception:
                pass

        return {
            "title":        market.get("question", ""),
            "link":         f"https://polymarket.com/event/{market.get('slug', '')}",
            "market_price": market_price,
            "fair_prob":    fair_prob,
            "edge":         edge,
            "roi":          (1.0 - market_price) / market_price * 100,
            "volume":       volume,
            "liquidity":    liquidity,
            "days_left":    days_left,
        }
    except Exception:
        return None

# ============================================================
# 📣 ФОРМАТИРОВАНИЕ АЛЕРТА
# ============================================================

def format_alert(r: dict) -> str:
    edge_pct   = r["edge"] * 100
    quality    = "🔥 СИЛЬНЫЙ" if edge_pct >= 20 else ("⚡ ХОРОШИЙ" if edge_pct >= 12 else "💡 СИГНАЛ")
    days_str   = f"{r['days_left']} дн." if r["days_left"] is not None else "—"

    return (
        f"{quality}\n\n"
        f"📋 <b>{r['title']}</b>\n\n"
        f"💰 Рыночная цена:  <b>{r['market_price']*100:.1f}%</b>\n"
        f"🎯 Наша оценка:     <b>{r['fair_prob']*100:.1f}%</b>\n"
        f"📈 Преимущество:   <b>+{edge_pct:.1f}%</b>\n"
        f"💵 ROI при победе: <b>{r['roi']:.0f}%</b>\n\n"
        f"📊 Объём:    ${r['volume']:,.0f}\n"
        f"💧 Ликвидность: ${r['liquidity']:,.0f}\n"
        f"⏳ До закрытия: {days_str}\n\n"
        f"🔗 <a href='{r['link']}'>Открыть на Polymarket</a>\n\n"
        f"⚠️ <i>Информационный алерт. Решение за тобой.</i>"
    )

# ============================================================
# 🔄 ПОТОК СКАНИРОВАНИЯ
# ============================================================

def scanner_loop():
    scan_count  = 0
    alert_count = 0
    seen        = set()

    print("[Scanner] Поток запущен")
    time.sleep(5)  # ждём пока бот стартует

    while True:
        scan_count += 1
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] Скан #{scan_count}")

        markets = get_active_markets()
        found   = []

        for market in markets:
            result = analyze_market(market)
            if result:
                key = f"{result['title']}_{result['market_price']:.3f}"
                if key not in seen:
                    found.append(result)
                    seen.add(key)
            time.sleep(0.3)

        # Очищаем seen раз в 100 сканов чтобы не копился
        if scan_count % 100 == 0:
            seen.clear()

        found.sort(key=lambda x: x["edge"], reverse=True)
        print(f"  Найдено новых сигналов: {len(found)}")

        for result in found[:3]:
            if send_telegram(format_alert(result)):
                alert_count += 1
                print(f"  ✅ {result['title'][:50]}... edge={result['edge']*100:.1f}%")
            time.sleep(2)

        print(f"  Всего алертов: {alert_count} | Следующий скан через {SCAN_INTERVAL//60} мин")
        time.sleep(SCAN_INTERVAL)

# ============================================================
# 🔄 ПОТОК ОБРАБОТКИ КОМАНД
# ============================================================

def polling_loop():
    print("[Polling] Поток запущен")
    while True:
        get_updates()
        time.sleep(3)

# ============================================================
# 🚀 ЗАПУСК
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("🤖 Polymarket Scanner Bot")
    print(f"   Token:    {'✅ задан' if TELEGRAM_TOKEN else '❌ не задан'}")
    print(f"   Chat ID:  {'✅ задан' if TELEGRAM_CHAT_ID else '❌ не задан'}")
    print(f"   Edge:     {MIN_EDGE*100:.0f}%")
    print(f"   Интервал: {SCAN_INTERVAL//60} мин")
    print("=" * 50)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n❌ Задай переменные окружения:")
        print("   TELEGRAM_TOKEN=твой_токен")
        print("   TELEGRAM_CHAT_ID=твой_chat_id")
        exit(1)

    send_telegram(
        "🚀 <b>Polymarket Scanner Bot запущен!</b>\n\n"
        f"• Edge порог: {MIN_EDGE*100:.0f}%\n"
        f"• Мин. объём: ${MIN_VOLUME:,.0f}\n"
        f"• Интервал: {SCAN_INTERVAL//60} мин\n\n"
        "Напиши /status чтобы проверить статус бота."
    )

    # Запускаем два потока параллельно
    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop, daemon=True)
    t1.start()
    t2.start()

    # Держим главный поток живым
    while True:
        time.sleep(60)
