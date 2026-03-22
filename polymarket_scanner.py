#!/usr/bin/env python3
"""
🎯 Polymarket Sure-Thing Bot v2
Главная фича: детектор "уже случившихся" событий
- Событие произошло, рынок ещё открыт, цена не 99¢
- Покупаешь 92-98¢, ждёшь резолюции, забираешь разницу
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

SCAN_INTERVAL  = int(os.environ.get("SCAN_INTERVAL", "120"))
DIGEST_HOUR    = int(os.environ.get("DIGEST_HOUR", "9"))

MIN_PRICE      = float(os.environ.get("MIN_PRICE", "0.88"))
MAX_PRICE      = float(os.environ.get("MAX_PRICE", "0.985"))
MIN_LIQUIDITY  = float(os.environ.get("MIN_LIQUIDITY", "3000"))
MIN_VOLUME     = float(os.environ.get("MIN_VOLUME", "5000"))
MIN_DAYS       = int(os.environ.get("MIN_DAYS", "0"))
MAX_DAYS       = int(os.environ.get("MAX_DAYS", "30"))

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ============================================================
# 🕵️  ДЕТЕКТОР "УЖЕ СЛУЧИВШИХСЯ" СОБЫТИЙ
# ============================================================

# Паттерны которые указывают что событие УЖЕ должно быть известно
ALREADY_HAPPENED_PATTERNS = [
    # Прошедшее время
    "did ", "was ", "were ", "has ", "have ", "had ",
    "won ", "lost ", "passed ", "failed ", "signed ", "announced ",
    "approved ", "rejected ", "confirmed ", "released ", "launched ",
    "reached ", "exceeded ", "dropped ", "rose ", "fell ",

    # Временные маркеры прошлого
    "by end of", "before end", "in q1", "in q2", "in q3", "in q4",
    "in january", "in february", "in march", "in april",
    "in may", "in june", "in july", "in august",
    "in september", "in october", "in november", "in december",
    "this week", "this month",
]

# Паттерны событий которые обычно известны заранее
PREDICTABLE_PATTERNS = [
    # Финансовые отчёты — дата известна
    "earnings", "report", "quarterly", "revenue", "gdp",
    "cpi", "inflation data", "jobs report", "unemployment",
    "fed meeting", "fomc", "rate decision", "interest rate",

    # Крипто — цена известна в реальном времени
    "bitcoin above", "bitcoin below", "btc above", "btc below",
    "ethereum above", "ethereum below", "eth above", "eth below",
    "crypto above", "crypto below",

    # Политические решения — часто уже приняты
    "bill passes", "vote passes", "legislation", "law signed",
    "treaty signed", "agreement signed", "deal signed",

    # Технологии — анонсы обычно известны
    "release", "launch", "announce", "unveil",
]

# Спорт — всегда исключаем
SPORTS_KEYWORDS = [
    "nba", "nfl", "nhl", "mlb", "ufc", "mma", "fifa",
    "game 1", "game 2", " vs ", "match", "score",
    "playoff", "championship", "tournament", "world cup",
    "super bowl", "f1 ", "grand prix", "tennis", "golf",
    "boxing", "esports", "lol:", "csgo", "dota"
]

def detect_already_happened(title: str, market: dict) -> dict:
    """
    Пытаемся определить произошло ли событие уже.
    Возвращаем: {
        "confidence": 0-100,  # уверенность что событие уже случилось
        "reason": "...",       # почему так думаем
        "type": "...",         # тип детекции
    }
    """
    t = title.lower()

    # 1. Спорт — пропускаем
    if any(kw in t for kw in SPORTS_KEYWORDS):
        return {"confidence": 0, "reason": "спорт", "type": "skip"}

    confidence = 0
    reasons    = []
    det_type   = "unknown"

    # 2. Крипто цена — можно проверить прямо сейчас
    crypto_check = check_crypto_price(title)
    if crypto_check["checked"]:
        if crypto_check["already_true"]:
            confidence = 95
            reasons.append(f"✅ Проверено: {crypto_check['detail']}")
            det_type = "crypto_verified"
        else:
            confidence = 5
            reasons.append(f"❌ Проверено: {crypto_check['detail']}")
            det_type = "crypto_verified"
        return {"confidence": confidence, "reason": " | ".join(reasons), "type": det_type}

    # 3. Паттерны прошедшего времени
    past_matches = [p for p in ALREADY_HAPPENED_PATTERNS if p in t]
    if past_matches:
        confidence += len(past_matches) * 15
        reasons.append(f"Паттерн прошлого: {', '.join(past_matches[:2])}")
        det_type = "past_pattern"

    # 4. Предсказуемые события
    pred_matches = [p for p in PREDICTABLE_PATTERNS if p in t]
    if pred_matches:
        confidence += len(pred_matches) * 10
        reasons.append(f"Предсказуемое: {', '.join(pred_matches[:2])}")
        if det_type == "unknown":
            det_type = "predictable"

    # 5. Цена уже высокая (95%+) — рынок уже "знает"
    op = market.get("outcomePrices", "[]")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except Exception:
            op = []
    if op:
        try:
            market_consensus = float(op[0])
            if market_consensus >= 0.97:
                confidence += 20
                reasons.append(f"Рынок уже оценивает в {market_consensus*100:.0f}%")
            elif market_consensus >= 0.94:
                confidence += 10
                reasons.append(f"Высокий консенсус {market_consensus*100:.0f}%")
        except Exception:
            pass

    # 6. Объём резко вырос (люди уже знают)
    volume    = float(market.get("volume", 0) or 0)
    liquidity = float(market.get("liquidity", 0) or 0)
    if volume > 0 and liquidity > 0:
        vol_liq_ratio = volume / max(liquidity, 1)
        if vol_liq_ratio > 10:
            confidence += 10
            reasons.append(f"Высокий оборот (x{vol_liq_ratio:.0f})")

    # Ограничиваем максимум без прямой проверки
    if det_type != "crypto_verified":
        confidence = min(confidence, 75)

    return {
        "confidence": confidence,
        "reason":     " | ".join(reasons) if reasons else "нет явных признаков",
        "type":       det_type,
    }

def check_crypto_price(title: str) -> dict:
    """
    Проверяем крипто условия напрямую через CoinGecko (бесплатно).
    Например: "Will BTC be above $80k by end of March?"
    """
    result = {"checked": False, "already_true": False, "detail": ""}
    t      = title.lower()

    # Определяем монету
    coin_map = {
        "bitcoin": "bitcoin", "btc": "bitcoin",
        "ethereum": "ethereum", "eth": "ethereum",
        "solana": "solana", "sol": "solana",
        "xrp": "ripple", "ripple": "ripple",
    }

    coin_id = None
    for keyword, cid in coin_map.items():
        if keyword in t:
            coin_id = cid
            break

    if not coin_id:
        return result

    # Извлекаем целевую цену из названия
    import re
    price_patterns = [
        r'\$(\d+[,\d]*(?:\.\d+)?)[kK]?',
    ]
    target_price = None
    for pattern in price_patterns:
        match = re.search(pattern, title)
        if match:
            price_str = match.group(1).replace(",", "")
            try:
                p = float(price_str)
                # Если число маленькое — может быть в тысячах
                if "k" in title[match.start():match.end()+1].lower():
                    p *= 1000
                target_price = p
                break
            except Exception:
                pass

    if not target_price:
        return result

    # Получаем текущую цену
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=8
        )
        if r.status_code == 200:
            data        = r.json()
            current_usd = data.get(coin_id, {}).get("usd", 0)
            if current_usd > 0:
                result["checked"] = True
                # Определяем направление
                if "above" in t or "over" in t or "exceed" in t or "higher" in t:
                    result["already_true"] = current_usd > target_price
                    result["detail"] = (
                        f"{coin_id.upper()} сейчас ${current_usd:,.0f} "
                        f"{'>' if current_usd > target_price else '<'} "
                        f"цель ${target_price:,.0f}"
                    )
                elif "below" in t or "under" in t or "lower" in t:
                    result["already_true"] = current_usd < target_price
                    result["detail"] = (
                        f"{coin_id.upper()} сейчас ${current_usd:,.0f} "
                        f"{'<' if current_usd < target_price else '>'} "
                        f"цель ${target_price:,.0f}"
                    )
    except Exception as e:
        print(f"  [CoinGecko Error] {e}")

    return result

# ============================================================
# 📰 НОВОСТИ — проверяем есть ли подтверждение события
# ============================================================

def check_news_confirmation(title: str) -> dict:
    """
    Ищем новости которые подтверждают что событие уже произошло.
    """
    result = {"confirmed": False, "count": 0, "headline": ""}
    try:
        words = title.split()[:6]
        query = "+".join(words)
        r     = requests.get(
            f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        if r.status_code != 200:
            return result

        root    = ET.fromstring(r.content)
        channel = root.find("channel")
        if channel is None:
            return result

        now   = datetime.now(timezone.utc)
        count = 0
        headlines = []

        # Слова подтверждения в заголовках
        confirm_words = [
            "confirms", "confirmed", "officially", "signs", "signed",
            "passes", "passed", "approves", "approved", "announces",
            "announced", "reaches", "exceeded", "hits", "achieves",
            "wins", "won", "loses", "lost", "releases", "launches"
        ]

        for item in channel.findall("item"):
            try:
                pub_date = item.findtext("pubDate", "")
                headline = item.findtext("title", "")
                if not pub_date or not headline:
                    continue
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_date)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                diff_h = (now - pub_dt).total_seconds() / 3600
                if diff_h <= 72:  # последние 72 часа
                    count += 1
                    h_lower = headline.lower()
                    if any(cw in h_lower for cw in confirm_words):
                        result["confirmed"] = True
                        if not result["headline"]:
                            result["headline"] = headline.split(" - ")[0][:80]
            except Exception:
                continue

        result["count"] = count

    except Exception as e:
        print(f"  [News Error] {e}")

    return result

# ============================================================
# 💾 ПАМЯТЬ
# ============================================================

signal_memory    = {}
tracked_markets  = {}
last_digest_date = None

def signal_key(title: str) -> str:
    return str(abs(hash(title)))[:12]

def already_sent(title: str, price: float) -> bool:
    key = signal_key(title)
    if key not in signal_memory:
        return False
    prev_price = signal_memory[key].get("price", 0)
    if price <= prev_price - 0.01:
        return False
    return True

def remember(result: dict):
    key = signal_key(result["title"])
    signal_memory[key] = {
        "price":      result["price"],
        "roi":        result["roi"],
        "sent_at":    datetime.now(timezone.utc).isoformat(),
        "title":      result["title"],
        "days":       result["days_left"],
        "confidence": result.get("confidence", 0),
        "type":       result.get("det_type", ""),
    }
    tracked_markets[result["market_id"]] = {
        "title":       result["title"],
        "entry_price": result["price"],
        "sent_at":     datetime.now(timezone.utc).isoformat(),
        "days_left":   result["days_left"],
    }

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
# 📡 POLLING + КОМАНДЫ
# ============================================================

last_update_id = 0

def get_updates():
    global last_update_id
    try:
        url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        params = {"offset": last_update_id + 1, "timeout": 5}
        r      = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            for upd in r.json().get("result", []):
                last_update_id = upd["update_id"]
                handle_command(upd)
    except Exception:
        pass

def handle_command(update: dict):
    text = update.get("message", {}).get("text", "")
    if not text:
        return
    if text == "/start":
        send_telegram(
            "👋 <b>Sure-Thing Bot v2</b>\n\n"
            "Ищу события которые <b>уже случились</b>\n"
            "но рынок ещё не закрылся.\n\n"
            "Типы сигналов:\n"
            "🔵 Крипто цена — проверяю прямо сейчас\n"
            "🟢 Подтверждено новостями\n"
            "🟡 Высокая вероятность по паттернам\n\n"
            f"• Диапазон: {MIN_PRICE*100:.0f}–{MAX_PRICE*100:.0f}¢\n"
            f"• Ликвидность: ${MIN_LIQUIDITY:,.0f}+\n\n"
            "Команды: /scan /status /results /digest /calc"
        )
    elif text == "/status":
        send_telegram(
            f"✅ <b>Бот работает</b>\n"
            f"🕐 {datetime.now().strftime('%H:%M %d.%m.%Y')}\n"
            f"💾 В памяти: {len(signal_memory)} сигналов\n"
            f"📊 Отслеживается: {len(tracked_markets)} рынков"
        )
    elif text == "/scan":
        send_telegram("🔍 Сканирую...")
        threading.Thread(target=manual_scan, daemon=True).start()
    elif text == "/results":
        threading.Thread(target=send_results, daemon=True).start()
    elif text == "/digest":
        threading.Thread(target=send_digest, daemon=True).start()
    elif text == "/calc":
        send_calc()

def send_calc():
    lines = ["🧮 <b>Калькулятор стратегии</b>\n"]
    for budget in [100, 500, 1000, 2000]:
        stake  = budget / 20
        # 19 побед из 20 при цене 0.94¢
        profit = 19 * stake * (1/0.94 - 1) - 1 * stake
        pct    = (profit / budget) * 100
        lines.append(
            f"💰 ${budget}: 20 ставок по ${stake:.0f} "
            f"→ <b>+${profit:.0f} ({pct:.1f}%)</b>"
        )
    lines.append("\n<i>*При 19/20 побед, цена входа 94¢</i>")
    send_telegram("\n".join(lines))

# ============================================================
# 🏆 РЕЗУЛЬТАТЫ ЗАКРЫТЫХ РЫНКОВ
# ============================================================

def check_closed_markets():
    to_remove = []
    for market_id, info in list(tracked_markets.items()):
        try:
            r = requests.get(f"{GAMMA_API}/markets/{market_id}", timeout=10)
            if r.status_code != 200:
                continue
            market = r.json()
            if market.get("active", True):
                continue
            op = market.get("outcomePrices", "[]")
            if isinstance(op, str):
                op = json.loads(op)
            winner = None
            if op and float(op[0]) >= 0.99:
                winner = "YES"
            elif len(op) > 1 and float(op[1]) >= 0.99:
                winner = "NO"
            if winner:
                ep  = info["entry_price"]
                won = (winner == "YES")
                if won:
                    profit = ((1.0 - ep) / ep) * 100
                    res    = f"✅ ВЫИГРАЛ +{profit:.1f}%"
                else:
                    res = f"❌ ПРОИГРАЛ -{ep*100:.0f}¢ на $1"
                send_telegram(
                    f"🏁 <b>Результат</b>\n\n"
                    f"{info['title']}\n\n"
                    f"Вход: {ep*100:.1f}¢ | Исход: {winner}\n"
                    f"<b>{res}</b>"
                )
                to_remove.append(market_id)
        except Exception as e:
            print(f"  [Results Error] {e}")
    for mid in to_remove:
        del tracked_markets[mid]

def send_results():
    if not tracked_markets:
        send_telegram("📭 Нет активных позиций.")
        return
    lines = ["📊 <b>Активные позиции:</b>\n"]
    for mid, info in list(tracked_markets.items())[:10]:
        ep  = info["entry_price"]
        roi = ((1.0 - ep) / ep) * 100
        lines.append(
            f"• {info['title'][:50]}\n"
            f"  Вход: {ep*100:.1f}¢ | ROI: +{roi:.1f}% | "
            f"Осталось: {info['days_left']} дн."
        )
    send_telegram("\n".join(lines))

# ============================================================
# ⏰ ДАЙДЖЕСТ
# ============================================================

def send_digest():
    now    = datetime.now(timezone.utc)
    recent = [
        info for info in signal_memory.values()
        if (now - datetime.fromisoformat(info["sent_at"])).total_seconds() <= 86400
    ]
    if not recent:
        send_telegram(
            f"☀️ <b>Дайджест {now.strftime('%d.%m.%Y')}</b>\n\n"
            "За 24ч новых сигналов не было."
        )
        return

    recent.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    lines = [
        f"☀️ <b>Дайджест {now.strftime('%d.%m.%Y')}</b>\n"
        f"Сигналов за 24ч: {len(recent)}\n"
    ]
    for i, info in enumerate(recent[:7], 1):
        lines.append(
            f"{i}. {info['title'][:55]}\n"
            f"   {info['price']*100:.1f}¢ | "
            f"+{info['roi']:.1f}% | "
            f"уверенность: {info.get('confidence', '?')}%"
        )
    send_telegram("\n".join(lines))

def digest_loop():
    global last_digest_date
    while True:
        now = datetime.now(timezone.utc)
        if now.hour == DIGEST_HOUR and now.date() != last_digest_date:
            send_digest()
            last_digest_date = now.date()
        time.sleep(60)

# ============================================================
# 📊 ПАРСИНГ РЫНКОВ
# ============================================================

def get_active_markets(limit: int = 200) -> list:
    try:
        params = {
            "active": "true", "closed": "false",
            "archived": "false", "limit": limit,
            "order": "liquidity", "ascending": "false",
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
            p = float(r.json().get("price", 0))
            return p if p > 0 else None
    except Exception:
        pass
    return None

def get_market_url(market: dict) -> str:
    """
    Правильный URL на Polymarket.
    Приоритет: groupSlug (event) → slug (market) → conditionId
    """
    # groupSlug — это slug события (работает всегда)
    group_slug = market.get("groupSlug", "")
    if group_slug:
        return f"https://polymarket.com/event/{group_slug}"

    # slug рынка — тоже работает через /event/
    slug = market.get("slug", "")
    if slug:
        return f"https://polymarket.com/event/{slug}"

    # conditionId — запасной вариант
    condition_id = market.get("conditionId", "")
    if condition_id:
        return f"https://polymarket.com/market/{condition_id}"

    return "https://polymarket.com"

def analyze_market(market: dict) -> dict | None:
    try:
        title     = market.get("question", "")
        volume    = float(market.get("volume", 0) or 0)
        liquidity = float(market.get("liquidity", 0) or 0)
        market_id = str(market.get("id", ""))

        # Спорт исключаем сразу
        t = title.lower()
        if any(kw in t for kw in SPORTS_KEYWORDS):
            return None

        if volume < MIN_VOLUME or liquidity < MIN_LIQUIDITY:
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
        end_date  = market.get("endDate", "")
        if end_date:
            try:
                end_dt    = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_left = (end_dt - datetime.now(timezone.utc)).days
            except Exception:
                pass

        if days_left is None or not (MIN_DAYS <= days_left <= MAX_DAYS):
            return None

        price = get_clob_price(token_id)
        if not price or not (MIN_PRICE <= price <= MAX_PRICE):
            return None

        # Детектор уже случившихся событий
        detection   = detect_already_happened(title, market)
        confidence  = detection["confidence"]
        det_type    = detection["type"]

        # Пропускаем если уверенность слишком низкая
        if confidence < 20:
            return None

        # Проверка новостями
        news = check_news_confirmation(title)
        if news["confirmed"]:
            confidence = min(confidence + 25, 99)

        roi = ((1.0 - price) / price) * 100

        # Скор надёжности
        reliability = 0
        if det_type == "crypto_verified":
            reliability += 50
        elif det_type == "past_pattern":
            reliability += 25
        elif det_type == "predictable":
            reliability += 15

        if news["confirmed"]:
            reliability += 30
        elif news["count"] > 5:
            reliability += 10

        if volume > 100_000:
            reliability += 20
        elif volume > 50_000:
            reliability += 12
        elif volume > 10_000:
            reliability += 5

        if liquidity > 50_000:
            reliability += 15
        elif liquidity > 10_000:
            reliability += 8

        if 3 <= days_left <= 14:
            reliability += 15
        elif days_left <= 2:
            reliability += 5

        return {
            "title":       title,
            "link":        get_market_url(market),
            "market_id":   market_id,
            "price":       price,
            "roi":         roi,
            "volume":      volume,
            "liquidity":   liquidity,
            "days_left":   days_left,
            "confidence":  confidence,
            "reliability": min(reliability, 99),
            "det_type":    det_type,
            "det_reason":  detection["reason"],
            "news":        news,
        }

    except Exception as e:
        print(f"  [Analyze Error] {e}")
        return None

# ============================================================
# 📣 ФОРМАТИРОВАНИЕ
# ============================================================

def format_alert(r: dict) -> str:
    price = r["price"]
    roi   = r["roi"]
    rel   = r["reliability"]
    conf  = r["confidence"]

    # Тип сигнала
    if r["det_type"] == "crypto_verified":
        sig_type = "🔵 КРИПТО — ПРОВЕРЕНО В РЕАЛЬНОМ ВРЕМЕНИ"
    elif r["news"]["confirmed"]:
        sig_type = "🟢 ПОДТВЕРЖДЕНО НОВОСТЯМИ"
    elif conf >= 60:
        sig_type = "🟡 ВЫСОКАЯ ВЕРОЯТНОСТЬ"
    else:
        sig_type = "🟠 УМЕРЕННАЯ ВЕРОЯТНОСТЬ"

    # Надёжность
    if rel >= 75:
        rel_str = f"🛡️ {rel}/100 — <b>очень надёжный</b>"
    elif rel >= 50:
        rel_str = f"🛡️ {rel}/100 — надёжный"
    else:
        rel_str = f"🛡️ {rel}/100 — умеренный"

    days_str = f"{r['days_left']} дн." if r["days_left"] > 0 else "сегодня"

    # Рекомендация по ставке
    if rel >= 75:
        stake = "5–8% бюджета"
    elif rel >= 50:
        stake = "2–4% бюджета"
    else:
        stake = "1–2% бюджета"

    # Новостное подтверждение
    news_str = ""
    if r["news"]["confirmed"] and r["news"]["headline"]:
        news_str = f"\n📰 <i>«{r['news']['headline']}»</i>"
    elif r["news"]["count"] > 0:
        news_str = f"\n📰 {r['news']['count']} новостей по теме за 72ч"

    # Причина детекции
    reason_str = f"\n🔍 <i>{r['det_reason']}</i>" if r["det_reason"] else ""

    return (
        f"{sig_type}\n\n"
        f"<b>{r['title']}</b>\n\n"
        f"💰 Цена входа:     <b>{price*100:.1f}¢</b>\n"
        f"💵 ROI при победе: <b>+{roi:.1f}%</b>\n"
        f"🎯 Уверенность:   <b>{conf}%</b>\n"
        f"{rel_str}{reason_str}{news_str}\n\n"
        f"📊 Объём:       ${r['volume']:,.0f}\n"
        f"💧 Ликвидность: ${r['liquidity']:,.0f}\n"
        f"⏳ До закрытия: {days_str}\n\n"
        f"💡 Рекомендуемая ставка: {stake}\n\n"
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
            key = result["title"]
            if key not in seen:
                found.append(result)
                seen.add(key)
        time.sleep(0.4)

    # Сортируем: сначала крипто-верифицированные, потом по надёжности
    found.sort(key=lambda x: (
        x["det_type"] == "crypto_verified",
        x["news"]["confirmed"],
        x["reliability"]
    ), reverse=True)

    print(f"  [{label}] Найдено: {len(found)}")

    sent = 0
    for result in found[:10]:
        if already_sent(result["title"], result["price"]):
            print(f"  ⏭️  Уже в памяти: {result['title'][:40]}")
            continue
        if send_telegram(format_alert(result)):
            remember(result)
            sent += 1
            print(
                f"  ✅ {result['title'][:45]}... "
                f"price={result['price']*100:.1f}¢ "
                f"roi=+{result['roi']:.1f}% "
                f"conf={result['confidence']}% "
                f"type={result['det_type']}"
            )
        time.sleep(1.5)

    if label == "manual" and sent == 0:
        send_telegram(
            "😔 Новых сигналов нет.\n\n"
            "Либо все уже в памяти,\n"
            "либо нет рынков с достаточной уверенностью.\n\n"
            f"Критерии: {MIN_PRICE*100:.0f}–{MAX_PRICE*100:.0f}¢, "
            f"ликвидность ${MIN_LIQUIDITY:,.0f}+"
        )
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
        if scan_count % 10 == 0:
            check_closed_markets()
        print(f"  Всего: {alert_count} алертов | Следующий через {SCAN_INTERVAL//60} мин")
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
    print("🎯 Sure-Thing Bot v2")
    print(f"   Token:    {'✅' if TELEGRAM_TOKEN else '❌'}")
    print(f"   Chat ID:  {'✅' if TELEGRAM_CHAT_ID else '❌'}")
    print(f"   Диапазон: {MIN_PRICE*100:.0f}–{MAX_PRICE*100:.0f}¢")
    print(f"   Интервал: {SCAN_INTERVAL//60} мин")
    print("=" * 50)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n❌ Задай переменные окружения")
        exit(1)

    send_telegram(
        "🎯 <b>Sure-Thing Bot v2 запущен!</b>\n\n"
        "Ищу события которые <b>уже случились</b>\n"
        "но рынок ещё не закрылся.\n\n"
        "Приоритеты:\n"
        "🔵 Крипто — проверяю цену в реальном времени\n"
        "🟢 Подтверждено свежими новостями\n"
        "🟡 Высокая вероятность по паттернам\n\n"
        f"Диапазон: {MIN_PRICE*100:.0f}–{MAX_PRICE*100:.0f}¢\n\n"
        "Команды: /scan /status /results /digest /calc"
    )

    t1 = threading.Thread(target=scanner_loop, daemon=True)
    t2 = threading.Thread(target=polling_loop,  daemon=True)
    t3 = threading.Thread(target=digest_loop,   daemon=True)
    t1.start()
    t2.start()
    t3.start()

    while True:
        time.sleep(60)
