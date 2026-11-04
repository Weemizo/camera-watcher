#!/usr/bin/env python3
"""Śledzi ogłoszenia aparatów na OLX i wysyła alerty na Telegram.

Bez zewnętrznych bibliotek i bez płatnych API: publiczne API OLX + darmowy Telegram Bot API.

Użycie:
  python watcher.py              # jedno sprawdzenie (tak działa w GitHub Actions)
  python watcher.py --dry-run    # pokaż pasujące oferty, nic nie wysyłaj i nie zapisuj
  python watcher.py --loop 600   # sprawdzaj co 600 s (gdy uruchamiasz na własnym PC)
  python watcher.py --chat-id    # wypisz swoje chat_id (najpierw napisz cokolwiek do bota)
  python watcher.py --test       # wyślij wiadomość testową
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
OLX_API = "https://www.olx.pl/api/v1/offers/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
STATE_TTL_DAYS = 120

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_dotenv():
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_json(url, data=None):
    body = None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------- OLX ----------

def olx_json(url):
    # OLX (CloudFront) odrzuca zapytania z urllib, a zwykły curl przepuszcza.
    # shutil.which: na Windows subprocess wziąłby curl z System32 zamiast tego z PATH (np. z Gita).
    out = subprocess.run([shutil.which("curl") or "curl","-sS", "--fail", "--max-time", "30", "-H", "User-Agent: Mozilla/5.0", url],
                         capture_output=True, check=True)
    return json.loads(out.stdout.decode("utf-8"))


def olx_search(query, category_id, price_from, price_to, max_pages=5):
    params = {
        "offset": 0,
        "limit": 50,
        "query": query,
        "category_id": category_id,
        "filter_float_price:from": price_from,
        "filter_float_price:to": price_to,
        "sort_by": "created_at:desc",
    }
    url = OLX_API + "?" + urllib.parse.urlencode(params)
    for _ in range(max_pages):
        page = olx_json(url)
        yield from page.get("data", [])
        nxt = (page.get("links") or {}).get("next")
        if not nxt or not page.get("data"):
            break
        url = nxt["href"]
        time.sleep(0.7)


def param(offer, key):
    for p in offer.get("params", []):
        if p.get("key") == key:
            return p.get("value") or {}
    return {}


def photo_url(offer):
    photos = offer.get("photos") or []
    if not photos:
        return None
    return photos[0]["link"].replace("{width}", "1000").replace("{height}", "750")


# ---------- dopasowanie ----------

def normalize(text):
    return html.unescape(text or "").lower()


def match_model(title, models, stuffing_markers=()):
    t = normalize(title)
    for marker in stuffing_markers:
        pos = t.find(marker)
        if pos != -1:
            t = t[:pos]
    for m in models:
        if re.search(m["pattern"], t):
            return m
    return None


def excluded(title, keywords):
    t = normalize(title)
    return any(k in t for k in keywords)


def evaluate(offer, cfg):
    """Zwraca (model, cena, próg) jeśli oferta jest okazją, inaczej None."""
    title = offer.get("title", "")
    model = match_model(title, cfg["models"], cfg.get("stuffing_markers", []))
    if not model or excluded(title, cfg["exclude_title_keywords"]):
        return None
    if param(offer, "state").get("key") == "damaged" or "uszkodz" in normalize(param(offer, "state").get("label")):
        return None
    price = param(offer, "price")
    value = price.get("value")
    if price.get("currency", "PLN") != "PLN" or not value:
        return None
    limit = model.get("max_price", cfg["max_price"])
    if price.get("negotiable"):
        limit += cfg.get("negotiable_bonus", 0)
    if not (cfg["min_price"] <= value <= limit):
        return None
    return model, value, limit


# ---------- Telegram ----------

def tg(method, payload):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Brak TELEGRAM_BOT_TOKEN (ustaw w .env albo w sekretach GitHuba).")
    return http_json(f"https://api.telegram.org/bot{token}/{method}", payload)


def chat_id():
    cid = os.environ.get("TELEGRAM_CHAT_ID")
    if not cid:
        raise SystemExit("Brak TELEGRAM_CHAT_ID. Uruchom: python watcher.py --chat-id")
    return cid


def send_alert(offer, model, value, prev_price):
    price = param(offer, "price")
    city = (offer.get("location") or {}).get("city", {}).get("name", "?")
    state = param(offer, "state").get("label", "")
    badges = []
    if prev_price:
        badges.append(f"📉 OBNIŻKA z {prev_price:g} zł")
    if price.get("negotiable"):
        badges.append("🤝 do negocjacji")
    if (offer.get("delivery") or {}).get("rock", {}).get("active"):
        badges.append("📦 Przesyłka OLX")
    desc = normalize(re.sub(r"<[^>]+>", " ", offer.get("description", "")))
    if re.search(r"japo[nń]|japan|jp version|wersja jp", desc + " " + normalize(offer.get("title"))):
        badges.append("🇯🇵 wersja japońska (zmiana języka zwykle łatwa)")
    caption = (
        f"📸 <b>{html.escape(model['name'])}</b> — <b>{value:g} zł</b>\n"
        f"{html.escape(html.unescape(offer['title']))}\n"
        f"📍 {html.escape(city)} · {html.escape(state)}\n"
        + ("\n".join(badges) + "\n" if badges else "")
        + f"\n{offer['url']}"
    )
    markup = {"inline_keyboard": [[{"text": "🛒 Otwórz na OLX", "url": offer["url"]}]]}
    photo = photo_url(offer)
    try:
        if photo:
            return tg("sendPhoto", {"chat_id": chat_id(), "photo": photo, "caption": caption[:1024],
                                    "parse_mode": "HTML", "reply_markup": markup})
    except Exception as e:
        print(f"  sendPhoto nie wyszedł ({e}), wysyłam sam tekst")
    return tg("sendMessage", {"chat_id": chat_id(), "text": caption, "parse_mode": "HTML",
                              "reply_markup": markup, "disable_web_page_preview": False})


# ---------- stan ----------

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"alerted": {}}


def save_state(state):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATE_TTL_DAYS)).isoformat()
    state["alerted"] = {k: v for k, v in state["alerted"].items() if v.get("at", "") >= cutoff}
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


# ---------- główna pętla ----------

def run_once(cfg, dry_run=False):
    state = load_state()
    alerted = state["alerted"]
    top_limit = max([m.get("max_price", cfg["max_price"]) for m in cfg["models"]] + [cfg["max_price"]])
    top_limit += cfg.get("negotiable_bonus", 0)

    deals = {}
    for model in cfg["models"]:
        for q in model["queries"]:
            try:
                for offer in olx_search(q, cfg["olx_category_id"], cfg["min_price"], top_limit):
                    hit = evaluate(offer, cfg)
                    if hit:
                        deals[str(offer["id"])] = (offer, *hit)
            except Exception as e:
                print(f"! błąd zapytania '{q}': {e}")
            time.sleep(0.7)

    to_send = []
    for oid, (offer, model, value, limit) in deals.items():
        prev = alerted.get(oid)
        if prev is None or value < prev["price"]:
            to_send.append((oid, offer, model, value, prev["price"] if prev else None))

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] okazji w progu: {len(deals)}, nowych/obniżonych: {len(to_send)}")
    to_send.sort(key=lambda x: x[3])
    cap = cfg.get("max_alerts_per_run", 15)
    for i, (oid, offer, model, value, prev_price) in enumerate(to_send):
        print(f"  {value:>6g} zł  {model['name']:<28} {html.unescape(offer['title'])[:60]}\n           {offer['url']}")
        if dry_run:
            continue
        if i < cap:
            try:
                send_alert(offer, model, value, prev_price)
            except Exception as e:
                print(f"  ! Telegram: {e}")
                continue
            time.sleep(1.1)
        alerted[oid] = {"price": value, "title": html.unescape(offer["title"])[:80],
                        "at": datetime.now(timezone.utc).isoformat()}
    if not dry_run and len(to_send) > cap:
        rest = to_send[cap:]
        lines = [f"…i jeszcze {len(rest)} ofert:"] + [f"• {v:g} zł — {o['url']}" for _, o, _, v, _ in rest[:30]]
        tg("sendMessage", {"chat_id": chat_id(), "text": "\n".join(lines), "disable_web_page_preview": True})
    if not dry_run:
        save_state(state)


def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Alerty cenowe aparatów z OLX na Telegram")
    ap.add_argument("--dry-run", action="store_true", help="tylko wypisz, nic nie wysyłaj")
    ap.add_argument("--loop", type=int, metavar="SEK", help="sprawdzaj co SEK sekund")
    ap.add_argument("--chat-id", action="store_true", help="wypisz chat_id z ostatnich wiadomości do bota")
    ap.add_argument("--test", action="store_true", help="wyślij wiadomość testową")
    args = ap.parse_args()

    if args.chat_id:
        updates = tg("getUpdates", {}).get("result", [])
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
                 for u in updates if "message" in u}
        if not chats:
            print("Brak wiadomości. Napisz cokolwiek do swojego bota na Telegramie i uruchom ponownie.")
        for cid, name in chats.items():
            print(f"TELEGRAM_CHAT_ID={cid}   ({name})")
        return
    if args.test:
        tg("sendMessage", {"chat_id": chat_id(), "text": "✅ Bot działa — tu będą przychodzić alerty o aparatach."})
        print("Wysłano wiadomość testową.")
        return

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if args.loop:
        while True:
            try:
                run_once(cfg, args.dry_run)
            except Exception as e:
                print(f"! {e}")
            time.sleep(args.loop)
    run_once(cfg, args.dry_run)


if __name__ == "__main__":
    main()
