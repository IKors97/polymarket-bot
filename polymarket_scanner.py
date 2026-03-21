#!/usr/bin/env python3
"""
🔍 Polymarket Scanner Bot v4
- Тренд цены за 24ч (растёт/падает/стабильно)
- Активность рынка (кол-во сделок за 24ч)
- Новостной контекст через Google News RSS
- Умный скоринг с учётом всех факторов
"""

import requests
import time
import json
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ============================================================
# ⚙️  НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

MIN_EDGE        = float(os.environ.get("MIN_EDGE", "0.03"))
MIN_VOLUME      = float(os.environ.get("MIN_VOLUME", "1000"))
SCAN_INTERVAL   = int(os.environ.get("SCAN_INTERVAL", "60"))
MIN_PROB        = 0.20
MAX_PRICE       = 0.80
MIN_DAYS_LEFT   = 1

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ============================================================
# 🏷️  КАТЕГОРИИ
# ============================================================

CATEGORIES = {
    "🗳️ Политика": [
        "election", "president", "vote", "poll", "senate", "congress",
        "republican", "democrat", "trump", "harris", "approval", "party",
        "minister", "chancellor", "government", "referendum", "candidate",
        "mayor", "governor", "impeach", "resign", "parliament"
    ],
    "₿ Крипто": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
        "coinbase", "binance", "solana", "sol", "xrp", "ripple",
        "defi", "token", "stablecoin", "fed", "rate", "inflation",
        "recession", "gdp", "interest rate", "s&p", "nasdaq"
    ],
    "🌍 Геополитика": [
        "war", "conflict", "invasion", "sanction", "nato", "treaty",
        "ceasefire", "nuclear", "missile", "iran", "russia", "ukraine",
        "china", "taiwan", "israel", "hamas", "coup", "attack", "strike",
        "troops", "military", "diplomat", "summit", "alliance"
    ],
    "🤖 Технологии/ИИ": [
        "openai", "chatgpt", "gpt", "claude", "anthropic", "gemini",
        "artificial intelligence", "ai model", "agi", "nvidia", "apple",
        "google", "microsoft", "meta", "spacex", "starship", "launch",
        "tesla", "ipo", "acquisition", "merger"
    ],
}

SPORTS_KEYWORDS = [
    "nba", "nfl", "nhl", "mlb", "ufc", "mma", "fifa", "premier league",
    "champions league", "la liga", "bundesliga", "serie a", "ligue 1",
    "game 1", "game 2", "game 3", "match", " vs ", "tournament",
    "playoff", "championship", "world cup", "super bowl", "formula 1",
    "f1 ", "grand prix", "tennis", "wimbledon", "golf", "pga",
    "boxing", "wrestling", "esports", "lol:", "csgo", "dota",
    "bilibili", "t1 ", "fnatic", "team liquid", "score", "innings",
    "quarter", "half time", "overtime", "wicket", "century"
]

def get_category(title: str) -> str | None:
    t = title.lower()
    if any(kw in t for kw in SPORTS_KEYWORDS):
        return None
    for category, keywords in CATEGORIES.items():
        if any(kw in t for kw in keywords):
            return category
    return None

# ============================================================
# 📨 TELEGRAM
# ============================================================

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG] Токен или chat_id не заданы!")
        return False
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, data=data, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[TG Error] {e}")
        return False

# ============================================================
# 📡 TELEGRAM POLLING
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
    msg  = update.get("message", {})
    text = msg.get("text", "")
    if not text:
        return
    if text == "/start":
        send_telegram(
            "👋 <b>Polymarket Scanner Bot v4</b>\n\n"
            "Категории:\n"
            "🗳️ Политика  ₿ Крипто\n"
            "🌍 Геополитика  🤖 ИИ/Тех\n"
            "⛔ Спорт исключён\n\n"
            "Анализирую:\n"
            "• Тренд цены за 24ч\n"
            "• Активность рынка\n"
            "• Новостной контекст\n"
            "• Внешние оценки (Metaculus/Manifold)\n\n"
            f"• Edge: {MIN_EDGE*100:.0f}% | Мин.шанс: {MIN_PROB*100:.0f}%\n"
            f"• Интервал: {SCAN_INTERVAL//60} мин\n\n"
            "Команды: /scan /status"
        )
    elif text == "/status":
        send_telegram(
            f"✅ <b>Бот работает</b>\n"
            f"🕐 {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
            f"⏱️ Интервал: {SCAN_INTERVAL//60} мин"
        )
    elif text == "/scan":
        send_telegram("🔍 Запускаю ручной скан...")
        threading.Thread(target=manual_scan, daemon=True).start()

# ============================================================
# 📉 ТРЕНД ЦЕНЫ ЗА 24Ч
# ============================================================

def get_price_trend(market_id: str) -> dict:
    """
    Получаем историю цен через Gamma API.
    Возвращаем: направление тренда, изменение в %, сигнал.
    """
    result = {"trend": "unknown", "change_pct": 0, "signal": "neutral", "trades_24h": 0}
    try:
        # История торгов
        r = requests.get(
            f"{GAMMA_API}/trades",
            params={"market": market_id, "limit": 100},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code != 200:
            return result

        trades = r.json()
        if not trades or not isinstance(trades, list):
            return result

        now = datetime.now(timezone.utc)
        trades_24h = []

        for t in trades:
            try:
                ts_str = t.get("timestamp") or t.get("createdAt", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                diff = (now - ts).total_seconds()
                if diff <= 86400:  # 24 часа
                    trades_24h.append(t)
            except Exception:
                continue

        result["trades_24h"] = len(trades_24h)

        if len(trades_24h) < 2:
            return result

        # Цена первой и последней сделки за 24ч
        prices = []
        for t in trades_24h:
            try:
                p = float(t.get("price", 0))
                if p > 0:
                    prices.append(p)
            except Exception:
                continue

        if len(prices) < 2:
            return result

        price_start = prices[-1]  # самая старая (24ч назад)
        price_now   = prices[0]   # самая свежая

        if price_start == 0:
            return result

        change_pct = ((price_now - price_start) / price_start) * 100
        result["change_pct"] = round(change_pct, 2)

        # Тренд
        if change_pct > 3:
            result["trend"]  = "up"
            result["signal"] = "caution"    # рынок уже растёт — поздно?
        elif change_pct < -3:
            result["trend"]  = "down"
            result["signal"] = "opportunity"  # рынок падает — возможность!
        else:
            result["trend"]  = "stable"
            result["signal"] = "neutral"

    except Exception as e:
        print(f"  [Trend Error] {e}")

    return result

# ============================================================
# 📰 НОВОСТНОЙ КОНТЕКСТ (Google News RSS — бесплатно)
# ============================================================

def get_news_context(title: str) -> dict:
    """
    Ищем свежие новости по теме события через Google News RSS.
    Возвращаем: кол-во новостей за 48ч, заголовки топ-2.
    """
    result = {"count": 0, "headlines": []}
    try:
        # Берём ключевые слова из названия рынка
        words = title.split()[:5]
        query = "+".join(words)

        r = requests.get(
            f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code != 200:
            return result

        # Парсим RSS
        root = ET.fromstring(r.content)
        channel = root.find("channel")
        if channel is None:
            return result

        now = datetime.now(timezone.utc)
        count = 0
        headlines = []

        for item in channel.findall("item"):
            try:
                pub_date = item.findtext("pubDate", "")
                if pub_date:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub_date)
                    # Делаем timezone-aware если нужно
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    diff_h = (now - pub_dt).total_seconds() / 3600
                    if diff_h <= 48:
                        count += 1
                        if len(headlines) < 2:
                            h = item.findtext("title", "")
                            if h:
                                # Убираем " - Source" в конце
                                h = h.split(" - ")[0][:80]
                                headlines.append(h)
            except Exception:
                continue

        result["count"]     = count
        result["headlines"] = headlines

    except Exception as e:
        print(f"  [News Error] {e}")

    return result

# ============================================================
# 🌐 ВНЕШНИЕ ОЦЕНКИ
# ============================================================

def get_metaculus_price(title: str) -> float | None:
    try:
        r = requests.get(
            "https://www.metaculus.com/api2/questions/",
            params={"search": title[:60], "status": "open", "limit": 3},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code == 200:
            for q in r.json().get("results", []):
                cp = q.get("community_prediction", {})
                if cp:
                    p = cp.get("full", {}).get("q2")
                    if p and 0 < p < 1:
                        return float(p)
    except Exception:
        pass
    return None

def get_manifold_price(title: str) -> float | None:
    try:
        r = requests.get(
            "https://api.manifold.markets/v0/search-markets",
            params={"term": title[:50], "limit": 3},
            timeout=10
        )
        if r.status_code == 200:
            for m in r.json():
                prob = m.get("probability")
                if prob and 0 < prob < 1:
                    return float(prob)
    except Exception:
        pass
    return None

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

def get_market_url(market: dict) -> str:
    slug      = market.get("slug", "")
    market_id = market.get("id", "")
    if slug:
        return f"https://polymarket.com/event/{slug}"
    elif market_id:
        return f"https://polymarket.com/market/{market_id}"
    return "https://polymarket.com"

def estimate_fair_probability(title: str, market: dict, category: str) -> tuple:
    if category in ("🗳️ Политика", "🌍 Геополитика"):
        p = get_metaculus_price(title)
        if p:
            return p, "Metaculus"
        p = get_manifold_price(title)
        if p:
            return p, "Manifold"
    try:
        op = market.get("outcomePrices", "[]")
        if isinstance(op, str):
            op = json.loads(op)
        if len(op) >= 2:
            p_yes, p_no = float(op[0]), float(op[1])
            total = p_yes + p_no
            if total > 0:
                return p_yes / total, "market_math"
    except Exception:
        pass
    return None, ""

# ============================================================
# 🧠 УМНЫЙ СКОРИНГ v4
# ============================================================

def smart_score(result: dict) -> float:
    score = 0.0

    # Базовый edge
    score += result["edge"] * 100 * 2.0

    # Внешний источник
    if result["source"] in ("Metaculus", "Manifold"):
        score += 15.0

    # Золотая зона цены 20-45%
    p = result["market_price"]
    if 0.20 <= p <= 0.45:
        score += 20.0
    elif 0.45 < p <= 0.60:
        score += 5.0

    # Ликвидность
    if result["liquidity"] > 50_000:
        score += 10.0
    elif result["liquidity"] > 10_000:
        score += 5.0

    # Оптимальный срок
    days = result.get("days_left")
    if days and 7 <= days <= 90:
        score += 10.0
    elif days and days <= 2:
        score -= 15.0

    # 📉 ТРЕНД — падающая цена это возможность
    trend = result.get("trend_data", {})
    signal = trend.get("signal", "neutral")
    change = trend.get("change_pct", 0)
    if signal == "opportunity":           # цена падала — рынок недооценён
        score += 20.0
    elif signal == "caution":             # цена росла — может быть поздно
        score -= 10.0

    # ⚡ АКТИВНОСТЬ — живой рынок надёжнее
    trades = trend.get("trades_24h", 0)
    if trades >= 50:
        score += 15.0
    elif trades >= 20:
        score += 8.0
    elif trades >= 5:
        score += 3.0
    elif trades == 0:
        score -= 10.0   # мёртвый рынок — цена ненадёжная

    # 📰 НОВОСТИ — свежие новости = рынок может не успеть обновиться
    news = result.get("news_data", {})
    news_count = news.get("count", 0)
    if news_count >= 10:
        score += 20.0   # горячая тема — высокий шанс движения
    elif news_count >= 5:
        score += 10.0
    elif news_count >= 2:
        score += 5.0

    return round(score, 1)

# ============================================================
# 🔬 АНАЛИЗ ОДНОГО РЫНКА
# ============================================================

def analyze_market(market: dict) -> dict | None:
    try:
        title     = market.get("question", "")
        volume    = float(market.get("volume", 0) or 0)
        liquidity = float(market.get("liquidity", 0) or 0)
        market_id = market.get("id", "")

        category = get_category(title)
        if not category:
            return None

        if volume < MIN_VOLUME or liquidity < 500:
            return None

        token_id = None
        ids = market.get("clobTokenIds")
        if isinstance(ids, str):
            ids = json.loads(ids)
        if isinstance(ids, list) and ids:
            token_id = ids[0]
        if not token_id:
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

        if days_left is not None and days_left < MIN_DAYS_LEFT:
            return None

        market_price = get_clob_price(token_id)
        if not market_price or not (MIN_PROB <= market_price <= MAX_PRICE):
            return None

        fair_prob, source = estimate_fair_probability(title, market, category)
        if fair_prob is None:
            return None

        edge = fair_prob - market_price
        if edge < MIN_EDGE:
            return None

        # Дополнительные данные
        trend_data = get_price_trend(market_id)
        news_data  = get_news_context(title)

        result = {
            "title":        title,
            "link":         get_market_url(market),
            "category":     category,
            "market_price": market_price,
            "fair_prob":    fair_prob,
            "source":       source,
            "edge":         edge,
            "roi":          (1.0 - market_price) / market_price * 100,
            "volume":       volume,
            "liquidity":    liquidity,
            "days_left":    days_left,
            "trend_data":   trend_data,
            "news_data":    news_data,
        }
        result["score"] = smart_score(result)
        return result

    except Exception as e:
        print(f"  [Analyze Error] {e}")
        return None

# ============================================================
# 📣 ФОРМАТИРОВАНИЕ АЛЕРТА
# ============================================================

def format_alert(r: dict) -> str:
    edge_pct = r["edge"] * 100
    score    = r.get("score", 0)

    if score >= 70:
        quality = "🔥 СИЛЬНЫЙ СИГНАЛ"
    elif score >= 45:
        quality = "⚡ ХОРОШИЙ СИГНАЛ"
    else:
        quality = "💡 СИГНАЛ"

    days_str = f"{r['days_left']} дн." if r["days_left"] is not None else "—"

    source_labels = {
        "Metaculus":   "📊 Metaculus",
        "Manifold":    "📊 Manifold",
        "market_math": "📐 Математика рынка",
    }
    source_str = source_labels.get(r["source"], r["source"])

    # Тренд
    trend      = r.get("trend_data", {})
    change_pct = trend.get("change_pct", 0)
    trades_24h = trend.get("trades_24h", 0)
    signal     = trend.get("signal", "neutral")

    if signal == "opportunity":
        trend_str = f"📉 {change_pct:+.1f}% за 24ч — <b>рынок падает, возможность!</b>"
    elif signal == "caution":
        trend_str = f"📈 {change_pct:+.1f}% за 24ч — рынок растёт (осторожно)"
    else:
        trend_str = f"➡️ {change_pct:+.1f}% за 24ч — стабильно"

    # Активность
    if trades_24h >= 50:
        activity_str = f"🔥 {trades_24h} сделок за 24ч (очень активный)"
    elif trades_24h >= 20:
        activity_str = f"⚡ {trades_24h} сделок за 24ч (активный)"
    elif trades_24h >= 5:
        activity_str = f"💤 {trades_24h} сделок за 24ч (умеренный)"
    else:
        activity_str = f"😴 {trades_24h} сделок за 24ч (низкая активность)"

    # Новости
    news      = r.get("news_data", {})
    news_count = news.get("count", 0)
    headlines  = news.get("headlines", [])

    if news_count >= 10:
        news_str = f"🔴 {news_count} новостей за 48ч — <b>горячая тема!</b>"
    elif news_count >= 5:
        news_str = f"🟡 {news_count} новостей за 48ч — активная тема"
    elif news_count >= 2:
        news_str = f"🟢 {news_count} новости за 48ч"
    else:
        news_str = f"⚪ Новостей почти нет"

    # Топ заголовки
    headlines_str = ""
    if headlines:
        headlines_str = "\n<i>» " + "</i>\n<i>» ".join(headlines) + "</i>"

    # Золотая зона
    tip = ""
    if 0.20 <= r["market_price"] <= 0.40:
        tip = "\n💎 <i>Золотая зона: низкая цена, высокий ROI</i>"

    return (
        f"{quality}\n"
        f"{r['category']} | Скор: <b>{score}</b>\n\n"
        f"<b>{r['title']}</b>\n\n"
        f"💰 Рыночная цена:  <b>{r['market_price']*100:.1f}%</b>\n"
        f"🎯 Наша оценка:    <b>{r['fair_prob']*100:.1f}%</b>\n"
        f"🔍 Источник:       {source_str}\n"
        f"📈 Преимущество:  <b>+{edge_pct:.1f}%</b>\n"
        f"💵 ROI при победе: <b>{r['roi']:.0f}%</b>{tip}\n\n"
        f"── Анализ рынка ──\n"
        f"{trend_str}\n"
        f"{activity_str}\n"
        f"{news_str}{headlines_str}\n\n"
        f"📊 Объём:       ${r['volume']:,.0f}\n"
        f"💧 Ликвидность: ${r['liquidity']:,.0f}\n"
        f"⏳ До закрытия: {days_str}\n\n"
        f"🔗 <a href='{r['link']}'>Открыть на Polymarket</a>\n\n"
        f"⚠️ <i>Информационный алерт. Решение за тобой.</i>"
    )

# ============================================================
# 🔄 СКАНИРОВАНИЕ
# ============================================================

def run_scan(label: str = "auto") -> int:
    markets = get_active_markets()
    found   = []
    seen    = set()

    for market in markets:
        result = analyze_market(market)
        if result:
            key = f"{result['title']}_{result['market_price']:.3f}"
            if key not in seen:
                found.append(result)
                seen.add(key)
        time.sleep(0.5)

    found.sort(key=lambda x: x["score"], reverse=True)
    print(f"  [{label}] Найдено сигналов: {len(found)}")

    sent = 0
    for result in found[:5]:
        if send_telegram(format_alert(result)):
            sent += 1
            print(
                f"  ✅ [{result['category']}] "
                f"{result['title'][:40]}... "
                f"score={result['score']} "
                f"edge={result['edge']*100:.1f}% "
                f"trend={result['trend_data'].get('signal','?')} "
                f"news={result['news_data'].get('count',0)}"
            )
        time.sleep(2)

    if label == "manual" and sent == 0:
        send_telegram("😔 Сигналов не найдено. Попробуй позже.")
    return sent

def manual_scan():
    run_scan("manual")

def scanner_loop():
    scan_count  = 0
    alert_count = 0
    print("[Scanner] Поток запущен")
    time.sleep(5)
    while True:
        scan_count += 1
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Скан #{scan_count}")
        sent = run_scan("auto")
        alert_count += sent
        print(f"  Всего алертов: {alert_count} | Следующий скан через {SCAN_INTERVAL//60} мин")
        time.sleep(SCAN_INTERVAL)

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
    print("🤖 Polymarket Scanner Bot v4")
    print(f"   Token:    {'✅ задан' if TELEGRAM_TOKEN else '❌ не задан'}")
    print(f"   Chat ID:  {'✅ задан' if TELEGRAM_CHAT_ID else '❌ не задан'}")
    print(f"   Edge:     {MIN_EDGE*100:.0f}%")
    print(f"   Мин.шанс: {MIN_PROB*100:.0f}%")
    print(f"   Интервал: {SCAN_INTERVAL//60} мин")
    print("=" * 50)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n❌ Задай переменные окружения:")
        print("   TELEGRAM_TOKEN=твой_токен")
        print("   TELEGRAM_CHAT_ID=твой_chat_id")
        exit(1)

    send_telegram(
        "🚀 <b>Polymarket Scanner Bot v4!</b>\n\n"
        "Категории: 🗳️ Политика | ₿ Крипто\n"
        "🌍 Геополитика | 🤖 ИИ/Тех\n"
        "⛔ Спорт исключён\n\n"
        "Анализирую:\n"
        "📉 Тренд цены за 24ч\n"
        "⚡ Активность рынка\n"
        "📰 Новостной контекст\n"
        "📊 Metaculus + Manifold\n\n"
        f"Edge: {MIN_EDGE*100:.0f}% | Мин.шанс: {MIN_PROB*100:.0f}%\n\n"
        "Команды: /scan /status"
    )

    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop, daemon=True)
    t1.start()
    t2.start()

    while True:
        time.sleep(60)
