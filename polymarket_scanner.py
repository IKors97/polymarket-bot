#!/usr/bin/env python3
"""
🔍 Polymarket — Сканер недооценённых рынков
============================================
Логика: сравниваем рыночную цену (что люди готовы платить)
с реальной статистической вероятностью события.
Если разница > порога — отправляем алерт в Telegram.

НЕ торгует автоматически — только информирует тебя.
Решение всегда за тобой.
"""

import requests
import time
import json
import os
from datetime import datetime, timezone

# ============================================================
# ⚙️  НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN   = 8731539023:AAHmbfE7gjB0AFQmGt6uo6-yS5CPzDLpD24    # @BotFather
TELEGRAM_CHAT_ID = 330101109        # @userinfobot

# Минимальная "недооценённость" чтобы алерт сработал
# 0.08 = рыночная цена отличается от нашей оценки на 8%+
MIN_EDGE = 0.08

# Минимальный объём торгов (USDC) — фильтруем мусорные рынки
MIN_VOLUME = 5_000

# Минимальная ликвидность в стакане
MIN_LIQUIDITY = 1_000

# Как часто сканировать (секунды)
SCAN_INTERVAL = 600  # 10 минут

# Максимальная цена контракта (не интересуют уже "дорогие" исходы)
MAX_PRICE = 0.80

# Минимальная цена (слишком дешёвые — обычно мусор)
MIN_PRICE = 0.03

# ============================================================
# 📡 API ENDPOINTS
# ============================================================

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ============================================================
# 📨 TELEGRAM
# ============================================================

def send_telegram(message: str) -> bool:
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
        print(f"  [TG Error] {e}")
        return False

# ============================================================
# 📊 ПОЛУЧИТЬ АКТИВНЫЕ РЫНКИ
# ============================================================

def get_active_markets(limit: int = 100) -> list:
    """Получаем активные рынки через Gamma API (публичный, без ключа)"""
    try:
        params = {
            "active":   "true",
            "closed":   "false",
            "archived": "false",
            "limit":    limit,
            "order":    "volume24hr",
            "ascending":"false",
        }
        r = requests.get(
            f"{GAMMA_API}/markets",
            params=params,
            timeout=15
        )
        if r.status_code != 200:
            print(f"  [Gamma API] Статус {r.status_code}")
            return []
        return r.json()
    except Exception as e:
        print(f"  [Gamma API Error] {e}")
        return []

# ============================================================
# 📈 ПОЛУЧИТЬ ЦЕНУ ИЗ СТАКАНА (CLOB)
# ============================================================

def get_clob_price(token_id: str) -> dict | None:
    """Берём реальную рыночную цену из стакана заявок"""
    try:
        r = requests.get(
            f"{CLOB_API}/price",
            params={"token_id": token_id, "side": "BUY"},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "buy_price":  float(data.get("price", 0)),
            }
    except Exception:
        pass
    return None

# ============================================================
# 🧠 ОЦЕНКА ВЕРОЯТНОСТИ (наша модель)
# ============================================================

def estimate_fair_probability(market: dict) -> float | None:
    """
    Простая эвристическая модель оценки справедливой вероятности.
    
    В реальной торговле ты заменяешь это своим анализом:
    - для спортивных событий: статистика команд
    - для политики: данные опросов
    - для крипты: on-chain метрики
    
    Здесь — базовая модель на основе истории рынка.
    """
    
    outcomes = market.get("outcomes", "[]")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            return None

    outcome_prices = market.get("outcomePrices", "[]")
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except Exception:
            return None

    if not outcomes or not outcome_prices:
        return None

    # Для бинарного рынка (Да/Нет)
    if len(outcomes) == 2 and len(outcome_prices) == 2:
        try:
            p_yes = float(outcome_prices[0])
            p_no  = float(outcome_prices[1])
            total = p_yes + p_no

            # Нормализуем (убираем спред маркет-мейкера)
            if total > 0:
                fair_prob = p_yes / total
                return fair_prob
        except Exception:
            pass

    return None

# ============================================================
# 🔎 АНАЛИЗ ОДНОГО РЫНКА
# ============================================================

def analyze_market(market: dict) -> dict | None:
    """
    Возвращает словарь с анализом если рынок интересен,
    иначе None.
    """
    try:
        # Базовые данные
        title       = market.get("question", "")
        slug        = market.get("slug", "")
        volume      = float(market.get("volume", 0) or 0)
        liquidity   = float(market.get("liquidity", 0) or 0)
        end_date_str= market.get("endDate", "")
        token_id    = None

        # Достаём token_id для CLOB запроса
        clob_rewards = market.get("clobTokenIds")
        if clob_rewards:
            if isinstance(clob_rewards, str):
                try:
                    ids = json.loads(clob_rewards)
                    token_id = ids[0] if ids else None
                except Exception:
                    pass
            elif isinstance(clob_rewards, list):
                token_id = clob_rewards[0] if clob_rewards else None

        # Фильтры
        if volume < MIN_VOLUME:
            return None
        if liquidity < MIN_LIQUIDITY:
            return None
        if not token_id:
            return None

        # Наша оценка вероятности
        fair_prob = estimate_fair_probability(market)
        if fair_prob is None:
            return None

        # Рыночная цена
        clob_data = get_clob_price(token_id)
        if not clob_data:
            return None

        market_price = clob_data["buy_price"]

        # Фильтр по цене
        if market_price < MIN_PRICE or market_price > MAX_PRICE:
            return None

        # Считаем edge (насколько рынок недооценивает вероятность)
        edge = fair_prob - market_price

        if edge < MIN_EDGE:
            return None

        # Потенциальный ROI если событие случится
        roi = (1.0 - market_price) / market_price * 100

        # Дней до закрытия
        days_left = None
        if end_date_str:
            try:
                end_dt    = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                now       = datetime.now(timezone.utc)
                days_left = (end_dt - now).days
            except Exception:
                pass

        link = f"https://polymarket.com/event/{slug}"

        return {
            "title":        title,
            "link":         link,
            "market_price": market_price,
            "fair_prob":    fair_prob,
            "edge":         edge,
            "roi":          roi,
            "volume":       volume,
            "liquidity":    liquidity,
            "days_left":    days_left,
        }

    except Exception as e:
        print(f"  [Analyze Error] {e}")
        return None

# ============================================================
# 📣 ФОРМАТИРОВАНИЕ АЛЕРТА
# ============================================================

def format_alert(result: dict) -> str:
    edge_pct    = result["edge"] * 100
    market_pct  = result["market_price"] * 100
    fair_pct    = result["fair_prob"] * 100

    # Оценка качества находки
    if edge_pct >= 20:
        quality = "🔥 СИЛЬНЫЙ СИГНАЛ"
    elif edge_pct >= 12:
        quality = "⚡ ХОРОШИЙ СИГНАЛ"
    else:
        quality = "💡 УМЕРЕННЫЙ СИГНАЛ"

    days_str = f"{result['days_left']} дн." if result["days_left"] is not None else "неизвестно"

    return (
        f"{quality}\n\n"
        f"📋 <b>{result['title']}</b>\n\n"
        f"💰 Рыночная цена:   <b>{market_pct:.1f}%</b>\n"
        f"🎯 Наша оценка:      <b>{fair_pct:.1f}%</b>\n"
        f"📈 Преимущество:    <b>+{edge_pct:.1f}%</b>\n"
        f"💵 ROI при победе:  <b>{result['roi']:.0f}%</b>\n\n"
        f"📊 Объём торгов:  ${result['volume']:,.0f}\n"
        f"💧 Ликвидность:    ${result['liquidity']:,.0f}\n"
        f"⏳ До закрытия:   {days_str}\n\n"
        f"🔗 <a href='{result['link']}'>Открыть на Polymarket</a>\n\n"
        f"⚠️ <i>Это информационный алерт. Решение за тобой.</i>"
    )

# ============================================================
# 🚀 ОСНОВНОЙ ЦИКЛ СКАНИРОВАНИЯ
# ============================================================

def run():
    print("=" * 55)
    print("🔍 Polymarket Scanner запущен")
    print(f"   Минимальный edge:    {MIN_EDGE*100:.0f}%")
    print(f"   Минимальный объём:   ${MIN_VOLUME:,}")
    print(f"   Интервал:            {SCAN_INTERVAL//60} минут")
    print("=" * 55)

    send_telegram(
        "🔍 <b>Polymarket Scanner запущен!</b>\n\n"
        f"Параметры:\n"
        f"• Минимальный edge: {MIN_EDGE*100:.0f}%\n"
        f"• Минимальный объём: ${MIN_VOLUME:,}\n"
        f"• Интервал сканирования: {SCAN_INTERVAL//60} мин\n\n"
        f"Жду интересных рынков... 👀"
    )

    scan_count  = 0
    alert_count = 0

    while True:
        scan_count += 1
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] Скан #{scan_count}")

        # Загружаем рынки
        markets = get_active_markets(limit=150)
        print(f"  Загружено рынков: {len(markets)}")

        found = []

        for market in markets:
            result = analyze_market(market)
            if result:
                found.append(result)
            time.sleep(0.3)  # пауза между запросами

        # Сортируем по силе сигнала
        found.sort(key=lambda x: x["edge"], reverse=True)

        print(f"  Найдено сигналов: {len(found)}")

        if found:
            # Отправляем топ-3 за скан чтобы не спамить
            for result in found[:3]:
                msg = format_alert(result)
                if send_telegram(msg):
                    alert_count += 1
                    print(f"  ✅ Алерт: {result['title'][:50]}...")
                time.sleep(2)
        else:
            print("  Недооценённых рынков не найдено")

        print(f"  Всего алертов отправлено: {alert_count}")
        print(f"  Следующий скан через {SCAN_INTERVAL//60} минут...")
        time.sleep(SCAN_INTERVAL)

# ============================================================
# ▶️  ЗАПУСК
# ============================================================

if __name__ == "__main__":
    if TELEGRAM_TOKEN == "ТВОЙ_ТОКЕН_БОТА":
        print("❌ Заполни TELEGRAM_TOKEN и TELEGRAM_CHAT_ID!")
        print()
        print("Инструкция:")
        print("1. Напиши @BotFather в Telegram → /newbot → скопируй токен")
        print("2. Напиши @userinfobot → скопируй свой id")
        print("3. Вставь оба значения в начало скрипта")
        print("4. Запусти: python polymarket_scanner.py")
    else:
        run()