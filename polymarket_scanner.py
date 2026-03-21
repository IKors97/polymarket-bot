#!/usr/bin/env python3
"""
🔍 Polymarket Scanner Bot v5
- Память сигналов (без спама, повтор только при росте скора)
- Результаты закрытых рынков
- Дневной дайджест в 9 утра
- Резкий рост объёма за последний час
- Тренд цены за 24ч + активность + новости
"""

import requests
import time
import json
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

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
DIGEST_HOUR     = int(os.environ.get("DIGEST_HOUR", "9"))  # час дайджеста UTC

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ============================================================
# 💾 ПАМЯТЬ СИГНАЛОВ
# ============================================================

# Структура: { "market_title_hash": { "score": 72, "sent_at": "2026-03-21T...", "price": 0.28 } }
signal_memory = {}

# Закрытые рынки для отслеживания результатов
# Структура: { "market_id": { "title": "...", "our_call": "YES", "entry_price": 0.28, "sent_at": "..." } }
tracked_markets = {}

# Флаг чтобы дайджест не слался дважды в один день
last_digest_date = None

def signal_key(title: str) -> str:
    return str(hash(title))[:12]

def should_send_signal(result: dict) -> bool:
    """Проверяем стоит ли отправлять сигнал или это спам"""
    key   = signal_key(result["title"])
    score = result["score"]
    price = result["market_price"]

    if key not in signal_memory:
        return True  # новый сигнал — отправляем

    prev = signal_memory[key]

    # Повторяем если скор вырос на 20+ пунктов
    if score >= prev["score"] + 20:
        return True

    # Повторяем если цена упала на 5%+ (новая возможность)
    if price <= prev["price"] - 0.05:
        return True

    # Повторяем не чаще раза в 6 часов даже если изменилось
    sent_at = datetime.fromisoformat(prev["sent_at"])
    if datetime.now(timezone.utc) - sent_at > timedelta(hours=6):
        if score >= prev["score"] + 5:
            return True

    return False

def remember_signal(result: dict):
    """Запоминаем отправленный сигнал"""
    key = signal_key(result["title"])
    signal_memory[key] = {
        "score":    result["score"],
        "price":    result["market_price"],
        "sent_at":  datetime.now(timezone.utc).isoformat(),
        "title":    result["title"],
        "category": result["category"],
        "edge":     result["edge"],
        "roi":      result["roi"],
    }

    # Добавляем в отслеживание для проверки результата
    market_id = result.get("market_id", key)
    tracked_markets[market_id] = {
        "title":       result["title"],
        "our_call":    "YES",  # мы всегда рекомендуем YES (недооценённый)
        "entry_price": result["market_price"],
        "fair_prob":   result["fair_prob"],
        "sent_at":     datetime.now(timezone.utc).isoformat(),
        "days_left":   result.get("days_left"),
        "category":    result["category"],
    }

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
    "game 1", "game 2", "game 3", " vs ", "tournament", "playoff",
    "championship", "world cup", "super bowl", "formula 1", "f1 ",
    "grand prix", "tennis", "wimbledon", "golf", "pga", "boxing",
    "wrestling", "esports", "lol:", "csgo", "dota", "bilibili",
    "t1 ", "fnatic", "team liquid", "innings", "wicket"
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
            "👋 <b>Polymarket Scanner Bot v5</b>\n\n"
            "Категории:\n"
            "🗳️ Политика  ₿ Крипто\n"
            "🌍 Геополитика  🤖 ИИ/Тех\n"
            "⛔ Спорт исключён\n\n"
            "Анализирую:\n"
            "• 📉 Тренд цены за 24ч\n"
            "• ⚡ Активность и объём\n"
            "• 📰 Новостной контекст\n"
            "• 💾 Память (без спама)\n"
            "• 🏆 Результаты закрытых рынков\n\n"
            f"Edge: {MIN_EDGE*100:.0f}% | Мин.шанс: {MIN_PROB*100:.0f}%\n\n"
            "Команды:\n"
            "/scan — ручной скан\n"
            "/status — статус бота\n"
            "/results — результаты сигналов\n"
            "/digest — дайджест прямо сейчас"
        )
    elif text == "/status":
        send_telegram(
            f"✅ <b>Бот работает</b>\n"
            f"🕐 {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
            f"⏱️ Интервал: {SCAN_INTERVAL//60} мин\n"
            f"💾 Сигналов в памяти: {len(signal_memory)}\n"
            f"🏆 Отслеживается рынков: {len(tracked_markets)}"
        )
    elif text == "/scan":
        send_telegram("🔍 Запускаю ручной скан...")
        threading.Thread(target=manual_scan, daemon=True).start()
    elif text == "/results":
        threading.Thread(target=send_results, daemon=True).start()
    elif text == "/digest":
        threading.Thread(target=send_digest, daemon=True).start()

# ============================================================
# 🏆 РЕЗУЛЬТАТЫ ЗАКРЫТЫХ РЫНКОВ
# ============================================================

def check_closed_markets():
    """Проверяем закрылись ли отслеживаемые рынки и каков результат"""
    if not tracked_markets:
        return

    to_remove = []

    for market_id, info in tracked_markets.items():
        try:
            r = requests.get(
                f"{GAMMA_API}/markets/{market_id}",
                timeout=10
            )
            if r.status_code != 200:
                continue

            market = r.json()
            active = market.get("active", True)

            if not active:
                # Рынок закрылся — узнаём результат
                winner_outcome = None
                outcome_prices = market.get("outcomePrices", "[]")
                if isinstance(outcome_prices, str):
                    outcome_prices = json.loads(outcome_prices)

                # Выигравший исход имеет цену 1.0
                if outcome_prices and float(outcome_prices[0]) >= 0.99:
                    winner_outcome = "YES"
                elif outcome_prices and len(outcome_prices) > 1 and float(outcome_prices[1]) >= 0.99:
                    winner_outcome = "NO"

                if winner_outcome:
                    our_call    = info["our_call"]
                    entry_price = info["entry_price"]
                    won         = (winner_outcome == our_call)

                    if won:
                        profit_pct = ((1.0 - entry_price) / entry_price) * 100
                        result_str = f"✅ ВЫИГРАЛ! ROI: +{profit_pct:.0f}%"
                    else:
                        result_str = f"❌ Не сработал. Потеря: -{entry_price*100:.0f}¢ на $1"

                    send_telegram(
                        f"🏆 <b>Результат сигнала</b>\n\n"
                        f"{info['category']}\n"
                        f"<b>{info['title']}</b>\n\n"
                        f"📌 Наш прогноз: {our_call}\n"
                        f"💰 Цена входа: {entry_price*100:.1f}¢\n"
                        f"🎯 Исход: {winner_outcome}\n"
                        f"{result_str}\n\n"
                        f"📅 Сигнал был: {info['sent_at'][:10]}"
                    )
                    to_remove.append(market_id)

        except Exception as e:
            print(f"  [Results Error] {e}")

    for mid in to_remove:
        del tracked_markets[mid]

def send_results():
    """Команда /results — показать статус всех отслеживаемых сигналов"""
    if not tracked_markets:
        send_telegram("📭 Нет активных отслеживаемых сигналов.")
        return

    lines = ["📊 <b>Активные сигналы:</b>\n"]
    for mid, info in list(tracked_markets.items())[:10]:
        days = info.get("days_left", "?")
        lines.append(
            f"• {info['category']} | {info['title'][:45]}...\n"
            f"  Вход: {info['entry_price']*100:.1f}% | Осталось: {days} дн."
        )

    send_telegram("\n".join(lines))

# ============================================================
# ⏰ ДНЕВНОЙ ДАЙДЖЕСТ
# ============================================================

def send_digest():
    """Топ-5 лучших сигналов из памяти за последние 24ч"""
    now = datetime.now(timezone.utc)

    recent = []
    for key, info in signal_memory.items():
        try:
            sent_at = datetime.fromisoformat(info["sent_at"])
            if (now - sent_at).total_seconds() <= 86400:
                recent.append(info)
        except Exception:
            continue

    if not recent:
        send_telegram(
            f"☀️ <b>Дайджест {now.strftime('%d.%m.%Y')}</b>\n\n"
            "За последние 24ч интересных сигналов не было."
        )
        return

    recent.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = recent[:5]

    lines = [f"☀️ <b>Дайджест {now.strftime('%d.%m.%Y')}</b>\n"
             f"Топ сигналов за 24ч:\n"]

    for i, info in enumerate(top, 1):
        lines.append(
            f"{i}. {info['category']} | Скор: {info.get('score', '?')}\n"
            f"   <b>{info['title'][:55]}</b>\n"
            f"   Edge: +{info['edge']*100:.1f}% | ROI: {info['roi']:.0f}%\n"
        )

    send_telegram("\n".join(lines))

def digest_loop():
    """Отправляем дайджест каждый день в DIGEST_HOUR UTC"""
    global last_digest_date
    print(f"[Digest] Поток запущен, дайджест в {DIGEST_HOUR}:00 UTC")
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == DIGEST_HOUR and now.date() != last_digest_date:
            print(f"[Digest] Отправляю дайджест...")
            send_digest()
            last_digest_date = now.date()
        time.sleep(60)

# ============================================================
# 📈 ОБЪЁМ ЗА ПОСЛЕДНИЙ ЧАС
# ============================================================

def get_volume_spike(market_id: str, total_volume: float) -> dict:
    """
    Сравниваем объём последнего часа с средним часовым за 24ч.
    Если в 3х+ раз больше — это спайк.
    """
    result = {"spike": False, "ratio": 1.0, "volume_1h": 0}
    try:
        r = requests.get(
            f"{GAMMA_API}/trades",
            params={"market": market_id, "limit": 200},
            timeout=10
        )
        if r.status_code != 200:
            return result

        trades = r.json()
        if not trades or not isinstance(trades, list):
            return result

        now = datetime.now(timezone.utc)
        vol_1h  = 0.0
        vol_24h = 0.0

        for t in trades:
            try:
                ts_str = t.get("timestamp") or t.get("createdAt", "")
                if not ts_str:
                    continue
                ts   = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                diff = (now - ts).total_seconds()
                amount = float(t.get("amount", 0) or 0)
                if diff <= 3600:
                    vol_1h  += amount
                if diff <= 86400:
                    vol_24h += amount
            except Exception:
                continue

        if vol_24h > 0:
            avg_hourly = vol_24h / 24
            if avg_hourly > 0:
                ratio = vol_1h / avg_hourly
                result["ratio"]     = round(ratio, 1)
                result["volume_1h"] = round(vol_1h)
                result["spike"]     = ratio >= 3.0  # в 3х раз выше среднего

    except Exception as e:
        print(f"  [Volume Spike Error] {e}")

    return result

# ============================================================
# 📉 ТРЕНД ЦЕНЫ ЗА 24Ч
# ============================================================

def get_price_trend(market_id: str) -> dict:
    result = {"trend": "unknown", "change_pct": 0, "signal": "neutral", "trades_24h": 0}
    try:
        r = requests.get(
            f"{GAMMA_API}/trades",
            params={"market": market_id, "limit": 100},
            timeout=10
        )
        if r.status_code != 200:
            return result

        trades = r.json()
        if not trades or not isinstance(trades, list):
            return result

        now        = datetime.now(timezone.utc)
        trades_24h = []

        for t in trades:
            try:
                ts_str = t.get("timestamp") or t.get("createdAt", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if (now - ts).total_seconds() <= 86400:
                    trades_24h.append(t)
            except Exception:
                continue

        result["trades_24h"] = len(trades_24h)

        if len(trades_24h) < 2:
            return result

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

        price_start = prices[-1]
        price_now   = prices[0]

        if price_start == 0:
            return result

        change_pct          = ((price_now - price_start) / price_start) * 100
        result["change_pct"] = round(change_pct, 2)

        if change_pct > 3:
            result["trend"]  = "up"
            result["signal"] = "caution"
        elif change_pct < -3:
            result["trend"]  = "down"
            result["signal"] = "opportunity"
        else:
            result["trend"]  = "stable"
            result["signal"] = "neutral"

    except Exception as e:
        print(f"  [Trend Error] {e}")

    return result

# ============================================================
# 📰 НОВОСТИ
# ============================================================

def get_news_context(title: str) -> dict:
    result = {"count": 0, "headlines": []}
    try:
        words = title.split()[:5]
        query = "+".join(words)
        r = requests.get(
            f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        if r.status_code != 200:
            return result

        root    = ET.fromstring(r.content)
        channel = root.find("channel")
        if channel is None:
            return result

        now = datetime.now(timezone.utc)
        count     = 0
        headlines = []

        for item in channel.findall("item"):
            try:
                pub_date = item.findtext("pubDate", "")
                if pub_date:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub_date)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    if (now - pub_dt).total_seconds() / 3600 <= 48:
                        count += 1
                        if len(headlines) < 2:
                            h = item.findtext("title", "").split(" - ")[0][:80]
                            if h:
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
# 📊 ПАРСИНГ
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
# 🧠 УМНЫЙ СКОРИНГ v5
# ============================================================

def smart_score(result: dict) -> float:
    score = 0.0

    score += result["edge"] * 100 * 2.0

    if result["source"] in ("Metaculus", "Manifold"):
        score += 15.0

    p = result["market_price"]
    if 0.20 <= p <= 0.45:
        score += 20.0
    elif 0.45 < p <= 0.60:
        score += 5.0

    if result["liquidity"] > 50_000:
        score += 10.0
    elif result["liquidity"] > 10_000:
        score += 5.0

    days = result.get("days_left")
    if days and 7 <= days <= 90:
        score += 10.0
    elif days and days <= 2:
        score -= 15.0

    trend  = result.get("trend_data", {})
    signal = trend.get("signal", "neutral")
    if signal == "opportunity":
        score += 20.0
    elif signal == "caution":
        score -= 10.0

    trades = trend.get("trades_24h", 0)
    if trades >= 50:
        score += 15.0
    elif trades >= 20:
        score += 8.0
    elif trades >= 5:
        score += 3.0
    elif trades == 0:
        score -= 10.0

    news = result.get("news_data", {})
    nc   = news.get("count", 0)
    if nc >= 10:
        score += 20.0
    elif nc >= 5:
        score += 10.0
    elif nc >= 2:
        score += 5.0

    # 📈 СПАЙК ОБЪЁМА — кто-то крупный входит прямо сейчас
    spike = result.get("volume_spike", {})
    if spike.get("spike"):
        ratio = spike.get("ratio", 1)
        score += min(ratio * 5, 25)  # максимум +25

    return round(score, 1)

# ============================================================
# 🔬 АНАЛИЗ РЫНКА
# ============================================================

def analyze_market(market: dict) -> dict | None:
    try:
        title     = market.get("question", "")
        volume    = float(market.get("volume", 0) or 0)
        liquidity = float(market.get("liquidity", 0) or 0)
        market_id = str(market.get("id", ""))

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

        days_left = None
        end_date  = market.get("endDate", "")
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

        trend_data   = get_price_trend(market_id)
        news_data    = get_news_context(title)
        volume_spike = get_volume_spike(market_id, volume)

        result = {
            "title":        title,
            "link":         get_market_url(market),
            "category":     category,
            "market_id":    market_id,
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
            "volume_spike": volume_spike,
        }
        result["score"] = smart_score(result)
        return result

    except Exception as e:
        print(f"  [Analyze Error] {e}")
        return None

# ============================================================
# 📣 ФОРМАТИРОВАНИЕ
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

    if trades_24h >= 50:
        activity_str = f"🔥 {trades_24h} сделок за 24ч"
    elif trades_24h >= 20:
        activity_str = f"⚡ {trades_24h} сделок за 24ч"
    else:
        activity_str = f"💤 {trades_24h} сделок за 24ч"

    # Спайк объёма
    spike     = r.get("volume_spike", {})
    spike_str = ""
    if spike.get("spike"):
        ratio     = spike.get("ratio", 1)
        vol_1h    = spike.get("volume_1h", 0)
        spike_str = f"\n📈 <b>СПАЙК ОБЪЁМА x{ratio}!</b> ${vol_1h:,.0f} за последний час"

    news       = r.get("news_data", {})
    news_count = news.get("count", 0)
    headlines  = news.get("headlines", [])

    if news_count >= 10:
        news_str = f"🔴 {news_count} новостей за 48ч — <b>горячая тема!</b>"
    elif news_count >= 5:
        news_str = f"🟡 {news_count} новостей за 48ч"
    elif news_count >= 2:
        news_str = f"🟢 {news_count} новости за 48ч"
    else:
        news_str = "⚪ Новостей почти нет"

    headlines_str = ""
    if headlines:
        headlines_str = "\n<i>» " + "</i>\n<i>» ".join(headlines) + "</i>"

    tip = ""
    if 0.20 <= r["market_price"] <= 0.40:
        tip = "\n💎 <i>Золотая зона: низкая цена, высокий ROI</i>"

    # Повторный сигнал?
    key      = signal_key(r["title"])
    repeat   = ""
    if key in signal_memory:
        prev_score = signal_memory[key].get("score", 0)
        repeat     = f"\n🔄 <i>Повторный сигнал (скор вырос: {prev_score}→{score})</i>"

    return (
        f"{quality}\n"
        f"{r['category']} | Скор: <b>{score}</b>{repeat}\n\n"
        f"<b>{r['title']}</b>\n\n"
        f"💰 Рыночная цена:  <b>{r['market_price']*100:.1f}%</b>\n"
        f"🎯 Наша оценка:    <b>{r['fair_prob']*100:.1f}%</b>\n"
        f"🔍 Источник:       {source_str}\n"
        f"📈 Преимущество:  <b>+{edge_pct:.1f}%</b>\n"
        f"💵 ROI при победе: <b>{r['roi']:.0f}%</b>{tip}\n\n"
        f"── Анализ рынка ──\n"
        f"{trend_str}\n"
        f"{activity_str}{spike_str}\n"
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
        if not should_send_signal(result):
            print(f"  ⏭️  Пропускаю (уже в памяти): {result['title'][:40]}")
            continue
        if send_telegram(format_alert(result)):
            remember_signal(result)
            sent += 1
            print(
                f"  ✅ [{result['category']}] "
                f"{result['title'][:40]}... "
                f"score={result['score']} "
                f"spike={result['volume_spike'].get('spike', False)}"
            )
        time.sleep(2)

    if label == "manual" and sent == 0:
        send_telegram("😔 Новых сигналов нет. Все уже в памяти или рынок спокойный.")
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

        # Каждые 10 сканов проверяем закрытые рынки
        if scan_count % 10 == 0:
            print("  [Results] Проверяю закрытые рынки...")
            check_closed_markets()

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
    print("🤖 Polymarket Scanner Bot v5")
    print(f"   Token:    {'✅ задан' if TELEGRAM_TOKEN else '❌ не задан'}")
    print(f"   Chat ID:  {'✅ задан' if TELEGRAM_CHAT_ID else '❌ не задан'}")
    print(f"   Edge:     {MIN_EDGE*100:.0f}%")
    print(f"   Мин.шанс: {MIN_PROB*100:.0f}%")
    print(f"   Интервал: {SCAN_INTERVAL//60} мин")
    print(f"   Дайджест: {DIGEST_HOUR}:00 UTC")
    print("=" * 50)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n❌ Задай переменные окружения:")
        print("   TELEGRAM_TOKEN=твой_токен")
        print("   TELEGRAM_CHAT_ID=твой_chat_id")
        exit(1)

    send_telegram(
        "🚀 <b>Polymarket Scanner Bot v5!</b>\n\n"
        "Категории: 🗳️ Политика | ₿ Крипто\n"
        "🌍 Геополитика | 🤖 ИИ/Тех\n"
        "⛔ Спорт исключён\n\n"
        "Новое в v5:\n"
        "💾 Память сигналов (без спама)\n"
        "🏆 Результаты закрытых рынков\n"
        "⏰ Дайджест каждый день в 9:00 UTC\n"
        "📈 Детектор спайков объёма\n\n"
        "Команды: /scan /status /results /digest"
    )

    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop, daemon=True)
    t3 = threading.Thread(target=digest_loop,  daemon=True)
    t1.start()
    t2.start()
    t3.start()

    while True:
        time.sleep(60)
