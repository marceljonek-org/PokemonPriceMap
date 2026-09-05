"""Denný sken. Stiahne katalógy, zaradí produkty, uloží históriu a latest.json.

Návratové kódy:
  0  všetko v poriadku
  2  tvrdé zlyhanie (povinný eshop nedostupný alebo prepad počtu položiek)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import adapters  # noqa: E402
import classify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
DOCS = ROOT / "docs"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en-US;q=0.7,en;q=0.6",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Connection": "keep-alive",
}

RETRY_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}
RETRIES = 3

# Voliteľná proxy pre eshopy označené `proxy: true` v shops.yaml.
# Bez týchto premenných sa nič nemení a sken ide priamo — proxy je doplnok,
# nie podmienka behu.
PROXY_URL = os.environ.get("SCRAPE_PROXY_URL", "").strip()
PROXY_TOKEN = os.environ.get("SCRAPE_PROXY_TOKEN", "").strip()
PORTFOLIO_TOKEN = os.environ.get("SCRAPE_PORTFOLIO_TOKEN", "").strip()


def via_proxy(url: str, shop: dict) -> str:
    """Prepošle URL cez Cloudflare Worker, ak je preň eshop označený."""
    if not PROXY_URL or not shop.get("proxy"):
        return url
    return (f"{PROXY_URL.rstrip('/')}?t={quote(PROXY_TOKEN, safe='')}"
            f"&url={quote(url, safe='')}")

# České a slovenské mutácie toho istého predajcu majú jeden sklad a jednu
# cenotvorbu. Do mediánu preto smú hlasovať raz — inak jedna firma pri produkte
# s tromi ponukami medián priamo určuje.
SELLER_OF = {
    "pompo-cz": "pompo", "pompo-sk": "pompo",
    "xzone-cz": "xzone", "xzone-sk": "xzone",
    "alza-cz": "alza", "alza-sk": "alza",
    "smarty-cz": "smarty", "smarty-sk": "smarty",
    "vesely-drak-cz": "vesely-drak", "vesely-drak-sk": "vesely-drak",
}
MIN_FOR_MEDIAN = 3        # menej ponúk = to nie je trhová cena, len cena predajcu
MIN_HISTORY_DAYS = 14     # kým nie je história dlhšia, "najnižšie doteraz" nič nehovorí

DROP_RATIO = 0.5          # menej než polovica dát oproti minule = zlyhanie
MIN_REQUIRED_OK = 0.8     # aspoň 80 % povinných eshopov musí vrátiť dáta
OUTLIER_LOW = 0.3         # cena pod 30 % predchádzajúcej = podozrivá
OUTLIER_HIGH = 3.0
UNDER_MARKET_MIN = 0.05   # 5 % pod mediánom = zaujímavé
UNDER_MARKET_MAX = 0.30   # nad 30 % = skôr chyba eshopu než príležitosť
ABSURD_RATIO = 0.25       # pod štvrtinou mediánu = takmer isto nie ten produkt
IN_PRINT_DAYS = 550       # ~18 mesiacov; potom sa set zvyčajne prestáva tlačiť


# ------------------------------------------------------------------ config

def load_yaml(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ------------------------------------------------------------------ fetch

async def get_with_retry(client: httpx.AsyncClient, url: str, shop: dict) -> httpx.Response:
    """Jeden pokus nestačí: eshopy občas vrátia 403 alebo spadnú do timeoutu
    len preto, že prišli tri požiadavky rýchlo za sebou. Skúšame trikrát
    s narastajúcou pauzou a s hlavičkou Referer, ktorá vyzerá ako preklik
    z úvodnej stránky.
    """
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        if attempt:
            await asyncio.sleep(1.5 * (2 ** (attempt - 1)))
        try:
            resp = await client.get(via_proxy(url, shop),
                                     headers={"Referer": shop["base"] + "/"})
            if resp.status_code in RETRY_STATUS and attempt < RETRIES - 1:
                last_error = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp)
                continue
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
    raise last_error if last_error else RuntimeError("neznáma chyba")


def save_snapshot(directory: Path | None, shop_id: str, index: int, body: str) -> None:
    """Uloží stiahnutú stránku, aby sa z nej dal obnoviť test fixture."""
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{shop_id}-{index}.html").write_text(body, encoding="utf-8")


async def fetch_shop(client: httpx.AsyncClient, shop: dict, defaults: dict,
                     snapshots: Path | None = None) -> dict:
    """Stiahne všetky vstupné URL eshopu vrátane stránkovania."""
    offers: list[adapters.Offer] = []
    errors: list[str] = []
    pages_seen: set[str] = set()
    max_pages = defaults.get("max_pages", 12)

    for entry in shop["urls"]:
        url, page = entry, 0
        while url and page < max_pages:
            if url in pages_seen:
                break
            pages_seen.add(url)
            try:
                resp = await get_with_retry(client, url, shop)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                body = resp.text
                save_snapshot(snapshots, shop["id"], page, body)
                found = adapters.parse(shop["adapter"], body, shop)
                offers.extend(found)
                if not found:
                    break          # prázdna strana = koniec stránkovania
                # Pri proxy je resp.url adresa Workera — relatívne odkazy na
                # ďalšiu stranu sa musia skladať voči skutočnej adrese eshopu.
                base_url = url if shop.get("proxy") and PROXY_URL else str(resp.url)
                url = adapters.next_page(shop["adapter"], body, base_url)
            except Exception as exc:                      # noqa: BLE001
                errors.append(f"{url} -> {type(exc).__name__}: {exc}")
                break
            page += 1

    unique = {}
    for offer in offers:
        unique.setdefault(offer.url or f"{offer.shop_id}:{offer.name}", offer)
    return {"shop": shop, "offers": list(unique.values()), "errors": errors}


async def fetch_shopify(client: httpx.AsyncClient, shop: dict, defaults: dict,
                        snapshots: Path | None = None) -> dict:
    """Shopify má verejné products.json — stránkuje sa parametrom page."""
    offers, errors = [], []
    for entry in shop["urls"]:
        for page in range(1, defaults.get("max_pages", 12) + 1):
            url = f"{entry.rstrip('/')}/products.json?limit=250&page={page}"
            try:
                resp = await get_with_retry(client, url, shop)
                resp.raise_for_status()
                save_snapshot(snapshots, shop["id"], page, resp.text)
                batch = adapters.parse("shopify", resp.text, shop)
            except Exception as exc:                      # noqa: BLE001
                errors.append(f"{url} -> {type(exc).__name__}: {exc}")
                break
            if not batch:
                break
            offers.extend(batch)
    unique = {o.url: o for o in offers}
    return {"shop": shop, "offers": list(unique.values()), "errors": errors}


async def fetch_all(shops: list[dict], defaults: dict,
                    snapshots: Path | None = None) -> list[dict]:
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    timeout = httpx.Timeout(40.0, connect=20.0)
    sem = asyncio.Semaphore(defaults.get("concurrency", 3))

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True,
                                 limits=limits, timeout=timeout, http2=True) as client:
        async def run(shop):
            async with sem:
                worker = fetch_shopify if shop["adapter"] == "shopify" else fetch_shop
                result = await worker(client, shop, defaults, snapshots)
                await asyncio.sleep(0.8)          # nezaťažovať eshop
                return result

        return await asyncio.gather(*(run(s) for s in shops))


async def fetch_from_worker(store: str, field: str) -> list[dict]:
    """Načíta zoznam z Cloudflare Workera (KV), kam ho zapisuje stránka —
    portfólio (`portfolio`/`holdings`) alebo cieľové ceny (`watchlist`/`watchlist`).

    Keď Worker nie je nastavený alebo nedostupný, vráti prázdny zoznam.
    Ani portfólio, ani cieľové ceny nikdy nesmú zhodiť sken cien.
    """
    if not (PROXY_URL and PORTFOLIO_TOKEN):
        return []
    url = (f"{PROXY_URL.rstrip('/')}?{store}=1"
           f"&t={quote(PORTFOLIO_TOKEN, safe='')}")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            items = resp.json().get(field) or []
            return items if isinstance(items, list) else []
    except Exception as exc:                                  # noqa: BLE001
        print(f"{store}: načítanie zo stránky zlyhalo ({type(exc).__name__})")
        return []


# ------------------------------------------------------------------ kurz

async def fetch_fx() -> dict:
    """CZK -> EUR z ECB. Pri zlyhaní použije posledný známy kurz."""
    cache = DATA / "fx.json"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.frankfurter.dev/v1/latest",
                                    params={"base": "CZK", "symbols": "EUR"})
            resp.raise_for_status()
            payload = resp.json()
            rate = float(payload["rates"]["EUR"])
            out = {"czk_eur": rate, "date": payload.get("date"), "stale": False}
            cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return out
    except Exception:                                     # noqa: BLE001
        if cache.exists():
            out = json.loads(cache.read_text(encoding="utf-8"))
            out["stale"] = True
            return out
        return {"czk_eur": 1 / 24.1, "date": None, "stale": True}


def to_eur(price: float, currency: str, fx: dict) -> float:
    if currency.upper() == "EUR":
        return round(price, 2)
    return round(price * fx["czk_eur"], 2)


# ------------------------------------------------------------------ história

def read_history() -> list[dict]:
    path = DATA / "history.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


HISTORY_FIELDS = [
    "date", "shop_id", "edition_id", "format_id", "variant", "packs", "name", "url",
    "price", "currency", "price_eur", "per_pack_eur", "in_stock", "image",
]


def append_history(rows: list[dict]) -> None:
    """Zapíše dnešný sken a nahradí prípadný skorší zápis z toho istého dňa.

    Bez tohto by každé opakované spustenie v ten istý deň pridalo ďalšiu kópiu
    riadkov. Poistka porovnávajúca objem dát by potom merala dnešok proti
    trojnásobku včerajška a beh by padal na neexistujúci prepad.
    """
    if not rows:
        return
    path = DATA / "history.csv"
    today = rows[0]["date"]
    previous: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8", newline="") as fh:
            previous = [r for r in csv.DictReader(fh) if r.get("date") != today]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        for row in previous + rows:
            writer.writerow({k: row.get(k, "") for k in HISTORY_FIELDS})


# ------------------------------------------------------------------ build

def build_rows(results: list[dict], fx: dict, today: str) -> tuple[list[dict], list[dict]]:
    rows, unknown = [], []
    for result in results:
        shop = result["shop"]
        for offer in result["offers"]:
            hit = classify.classify(offer.name)
            if hit is None:
                # Zaujíma nás len to, čo vyzerá ako sledovaný formát v neznámej
                # edícii — tam sa prejaví novo vydaný set. Zvyšok je šum.
                if classify.looks_like_new_edition(offer.name):
                    unknown.append({"date": today, "shop_id": shop["id"],
                                    "name": offer.name, "url": offer.url})
                continue
            price_eur = to_eur(offer.price, offer.currency, fx)
            rows.append({
                "date": today,
                "shop_id": shop["id"],
                "edition_id": hit.edition.id,
                "format_id": hit.format.id,
                "variant": hit.variant,
                "packs": hit.packs if hit.packs else "",
                "name": offer.name,
                "url": offer.url,
                "price": round(offer.price, 2),
                "currency": offer.currency.upper(),
                "price_eur": price_eur,
                "per_pack_eur": round(price_eur / hit.packs, 2) if hit.packs else "",
                "in_stock": "1" if offer.in_stock else "0",
                # Text o dostupnosti sa nezapisuje do histórie, ale v detaile
                # ponuky je podstatný: "skladom" a "skladom u dodávateľa do 7 dní"
                # nie je to isté a appka to dovtedy zlievala do jedného áno.
                "stock_text": (offer.stock_text or "")[:60],
                "image": offer.image,
            })
    return dedupe_rows(rows), unknown


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Jeden eshop môže ten istý produkt uvádzať viackrát (varianty, duplicity).

    Pre cenový monitor je zmysluplná jednotka „najlacnejšia ponuka daného
    produktu v danom eshope“ — inak by sa medzidenné porovnanie robilo raz na
    jednu a raz na druhú položku a hlásilo by skoky, ktoré sa nestali.
    """
    best: dict[tuple, dict] = {}
    for row in rows:
        key = (row["shop_id"], row["edition_id"], row["format_id"], row.get("variant", ""))
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        better_stock = (row["in_stock"], current["in_stock"]) == ("1", "0")
        same_stock = row["in_stock"] == current["in_stock"]
        if better_stock or (same_stock and float(row["price_eur"]) < float(current["price_eur"])):
            best[key] = row
    return list(best.values())


def previous_snapshot(history: list[dict]) -> tuple[str | None, dict]:
    if not history:
        return None, {}
    last_date = max(r["date"] for r in history)
    snapshot = {(r["shop_id"], r["edition_id"], r["format_id"], r.get("variant", "")): r
                for r in history if r["date"] == last_date}
    return last_date, snapshot


def check_health(results: list[dict], history: list[dict], rows: list[dict]) -> tuple[list[str], list[str]]:
    """Rozhodne, či sa dáta smú zapísať.

    Pri 26 eshopoch je výpadok jedného normálna prevádzka, nie dôvod zahodiť
    celý beh — inak by monitor nefungoval nikdy. Fatálne je až to, keď vypadne
    väčšia časť povinných eshopov alebo keď celkový objem dát spadne na zlomok
    včerajška. Jednotlivé výpadky sú varovania: vypíšu sa a zobrazia v pätičke
    stránky, ale beh pokračuje.
    """
    fatal, warnings = [], []

    required = [r for r in results if not r["shop"].get("optional")]
    ok_required = [r for r in required if r["offers"]]
    if required:
        ratio = len(ok_required) / len(required)
        if ratio < MIN_REQUIRED_OK:
            fatal.append(f"len {len(ok_required)} z {len(required)} povinných eshopov "
                         f"vrátilo dáta (minimum je {round(MIN_REQUIRED_OK * 100)} %)")

    for result in results:
        if not result["offers"]:
            label = "nepovinný" if result["shop"].get("optional") else "povinný"
            first = result["errors"][0][:120] if result["errors"] else "bez chyby"
            warnings.append(f"{result['shop']['name']} ({label}) nevrátil nič — {first}")

    last_date, previous = previous_snapshot(history)
    if last_date:
        before_total = sum(1 for row in history if row["date"] == last_date)
        if before_total >= 20 and len(rows) < before_total * DROP_RATIO:
            fatal.append(f"celkovo {len(rows)} zaradených ponúk oproti {before_total} "
                         f"v behu z {last_date}")

        previous_counts = defaultdict(int)
        for row in history:
            if row["date"] == last_date:
                previous_counts[row["shop_id"]] += 1
        for result in results:
            before = previous_counts.get(result["shop"]["id"], 0)
            now = len([r for r in rows if r["shop_id"] == result["shop"]["id"]])
            if before >= 4 and now < before * DROP_RATIO:
                warnings.append(f"{result['shop']['name']}: {now} zaradených položiek "
                                f"oproti {before} v poslednom behu")
    return fatal, warnings


def flag_offer(price_eur: float, median_eur: float | None) -> str:
    if not median_eur or median_eur <= 0:
        return ""
    diff = (median_eur - price_eur) / median_eur
    if diff >= UNDER_MARKET_MAX:
        return f"overiť — {round(diff * 100)} % pod trhom"
    if diff >= UNDER_MARKET_MIN:
        return f"pod trhom {round(diff * 100)} %"
    return ""


def build_products(rows: list[dict], history: list[dict], images: dict,
                   today: str) -> tuple[list[dict], dict]:
    last_date, previous = previous_snapshot(history)
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["edition_id"], row["format_id"], row.get("variant", ""))].append(row)

    # denné minimum skladom na produkt, pre sparkliny
    series = defaultdict(dict)
    for row in history + rows:
        if row.get("in_stock") not in ("1", 1, True):
            continue
        key = (row["edition_id"], row["format_id"], row.get("variant", ""))
        day = row["date"]
        value = float(row["price_eur"])
        current = series[key].get(day)
        series[key][day] = value if current is None else min(current, value)

    products, movements = [], {"restocked": [], "sold_out": [], "price_drop": [], "new": []}

    for (edition_id, format_id, variant), offers in sorted(grouped.items()):
        edition = classify.edition_by_id(edition_id)
        fmt = classify.format_by_id(format_id)
        if edition is None or fmt is None:
            continue
        in_stock = [o for o in offers if o["in_stock"] == "1"]
        # Jedna firma = jeden hlas. Z dvojičiek pompo.cz/pompo.sk berieme lacnejšiu.
        per_seller: dict[str, float] = {}
        for offer in in_stock:
            seller = SELLER_OF.get(offer["shop_id"], offer["shop_id"])
            price = float(offer["price_eur"])
            if seller not in per_seller or price < per_seller[seller]:
                per_seller[seller] = price
        prices_in_stock = sorted(per_seller.values())
        median_eur = statistics.median(prices_in_stock) if prices_in_stock else None

        # Druhý prechod: ponuka hlboko pod mediánom skoro nikdy nie je ten
        # produkt — býva to súčiastka vybraná z balenia alebo chybný odčet.
        # Nechávame ju v tabuľke so značkou `overiť`, ale nesmie určovať
        # "najlacnejšie skladom" ani historické minimum.
        trusted = [p for p in prices_in_stock
                   if median_eur is None or p >= median_eur * (1 - UNDER_MARKET_MAX)]
        if trusted and len(trusted) < len(prices_in_stock):
            median_eur = statistics.median(trusted)
        min_eur = trusted[0] if trusted else None
        # Medián z jednej či dvoch ponúk nie je trhová cena — je to cena toho
        # predajcu. Príznak nesie ďalej frontend aj rebríček, aby sa z toho
        # nepočítalo "pod trhom" ani body za zľavu.
        sellers_in_stock = len(trusted)
        median_trusted = sellers_in_stock >= MIN_FOR_MEDIAN

        key = f"{edition_id}|{format_id}|{variant}" if variant else f"{edition_id}|{format_id}"
        packs = offers[0]["packs"] or None
        if isinstance(packs, str):
            packs = int(packs) if packs.isdigit() else None
        title = (variant.replace("-", " ").title() if variant
                 else f"{edition.name} — {fmt.name}")
        image = images.get(key) or next((o["image"] for o in offers if o["image"]), "")

        offer_list = []
        for offer in sorted(offers, key=lambda o: float(o["price_eur"])):
            price_eur = float(offer["price_eur"])
            prev = previous.get((offer["shop_id"], edition_id, format_id, variant))
            delta = None
            if prev:
                prev_price = float(prev["price_eur"])
                if prev_price > 0:
                    delta = round((price_eur - prev_price) / prev_price * 100, 1)
                    if price_eur < prev_price * OUTLIER_LOW or price_eur > prev_price * OUTLIER_HIGH:
                        offer["outlier"] = True
                if prev["in_stock"] != offer["in_stock"]:
                    bucket = "restocked" if offer["in_stock"] == "1" else "sold_out"
                    movements[bucket].append({
                        "key": key, "product": title,
                        "shop_id": offer["shop_id"], "price_eur": price_eur,
                        "url": offer["url"],
                    })
                if delta is not None and delta <= -3 and offer["in_stock"] == "1":
                    movements["price_drop"].append({
                        "key": key, "product": title,
                        "shop_id": offer["shop_id"], "price_eur": price_eur,
                        "delta": delta, "url": offer["url"],
                    })
            elif last_date:
                movements["new"].append({
                    "key": key, "product": title,
                    "shop_id": offer["shop_id"], "price_eur": price_eur,
                    "url": offer["url"],
                })
            offer_list.append({
                "shop_id": offer["shop_id"],
                "price": float(offer["price"]),
                "currency": offer["currency"],
                "price_eur": price_eur,
                "per_pack_eur": float(offer["per_pack_eur"]) if offer["per_pack_eur"] else None,
                "in_stock": offer["in_stock"] == "1",
                "url": offer["url"],
                "name": offer["name"],
                "delta_pct": delta,
                "flag": (flag_offer(price_eur, median_eur)
                         if offer["in_stock"] == "1" and median_trusted else ""),
                "stock_text": offer.get("stock_text", ""),
                "outlier": bool(offer.get("outlier")),
            })

        # Historické body čistíme len od nezmyslov (pod štvrtinou dnešného
        # mediánu) — ceny pri vydaní bývajú legitímne oveľa nižšie než dnes.
        raw_points = sorted(series[(edition_id, format_id, variant)].items())
        floor = median_eur * ABSURD_RATIO if median_eur else 0
        points = [(d, v) for d, v in raw_points if v >= floor][-60:]
        min_any = min(float(o["price_eur"]) for o in offers)
        min_delta = None
        if min_eur is not None and len(points) >= 2 and points[-1][0] == today:
            before = points[-2][1]
            if before > 0:
                min_delta = round((min_eur - before) / before * 100, 1)
        values = [v for _, v in points]
        launch = classify.launch_price(format_id)
        vs_launch = None
        if launch and min_eur:
            vs_launch = round((min_eur - launch) / launch * 100, 1)
        days_since = None
        in_print = None
        if edition.released:
            try:
                released_on = date.fromisoformat(edition.released)
                days_since = (date.fromisoformat(today) - released_on).days
                in_print = days_since < IN_PRINT_DAYS
            except ValueError:
                days_since = None
        products.append({
            "key": key,
            "title": title,
            "edition": {"id": edition.id, "name": edition.name, "code": edition.code,
                        "tier": edition.tier, "series": edition.series},
            "format": {"id": fmt.id, "name": fmt.name, "short": fmt.short},
            "variant": variant,
            "packs": packs,
            "low_eur": round(min(values), 2) if values else None,
            "high_eur": round(max(values), 2) if values else None,
            "launch_eur": launch,
            "vs_launch_pct": vs_launch,
            "released": edition.released,
            "days_since_release": days_since,
            "in_print": in_print,
            "image": image,
            "min_eur": min_eur,
            "min_any_eur": round(min_any, 2),
            "min_delta_pct": min_delta,
            "median_eur": round(median_eur, 2) if median_eur else None,
            "median_trusted": median_trusted,
            "sellers_in_stock": sellers_in_stock,
            "min_per_pack_eur": round(min_eur / packs, 2) if (min_eur and packs) else None,
            "offer_count": len(offers),
            "in_stock_count": len(in_stock),
            "history": [{"d": d, "v": v} for d, v in points],
            "offers": offer_list,
        })

    order = {"A": 0, "B": 1, "C": 2, "": 3}
    products.sort(key=lambda p: (order.get(p["edition"]["tier"], 9),
                                 p["edition"]["name"], p["format"]["id"], p["variant"]))
    return products, movements


# ------------------------------------------------------------------ portfólio

def build_portfolio(products: list[dict], config: dict, fx: dict) -> dict:
    """Porovná nákupné ceny s aktuálnym trhom.

    Oceňuje sa retailovou cenou skladom — mediánom alebo najlacnejšou ponukou.
    Je to len orientačné: cena, za ktorú sa produkt v obchode PONÚKA, nie je
    cena, za ktorú ho vieš predať. Reálne speňaženie býva nižšie.
    """
    basis = (config.get("valuation") or "median").lower()
    holdings = config.get("holdings") or []
    by_key = {p["key"]: p for p in products}

    items, cost_total, value_total = [], 0.0, 0.0
    for entry in holdings:
        key = str(entry.get("key") or "").strip()
        product = by_key.get(key)
        qty = float(entry.get("qty") or 0)
        price = float(entry.get("price") or 0)
        currency = str(entry.get("currency") or "EUR").upper()
        unit_cost = to_eur(price, currency, fx) if price else 0.0

        unit_value = None
        market_min = product["min_eur"] if product else None
        if product:
            unit_value = product["median_eur"] if basis == "median" else product["min_eur"]
            if unit_value is None:
                unit_value = product["min_eur"] or product["median_eur"] or product["min_any_eur"]

        cost = round(unit_cost * qty, 2)
        value = round(unit_value * qty, 2) if unit_value else None
        cost_total += cost
        if value:
            value_total += value

        items.append({
            "key": key,
            "title": product["title"] if product else key,
            "found": bool(product),
            "qty": qty,
            "unit_cost_eur": round(unit_cost, 2),
            "cost_eur": cost,
            "unit_value_eur": round(unit_value, 2) if unit_value else None,
            "value_eur": value,
            "pl_eur": round(value - cost, 2) if value else None,
            "pl_pct": round((value - cost) / cost * 100, 1) if (value and cost) else None,
            "market_min_eur": market_min,
            "vs_market_pct": (round((unit_cost - market_min) / market_min * 100, 1)
                              if (market_min and unit_cost) else None),
            "bought": str(entry.get("bought") or ""),
            "shop": str(entry.get("shop") or ""),
            "note": str(entry.get("note") or ""),
            "price": price,
            "currency": currency,
        })

    return {
        "basis": basis,
        "items": items,
        "totals": {
            "cost_eur": round(cost_total, 2),
            "value_eur": round(value_total, 2),
            "pl_eur": round(value_total - cost_total, 2),
            "pl_pct": (round((value_total - cost_total) / cost_total * 100, 1)
                       if cost_total else None),
            "unmatched": sum(1 for i in items if not i["found"]),
        },
    }


# ------------------------------------------------------------------ odporúčania

TIER_POINTS = {"A": 10, "B": 6, "C": 2, "": 3}
MAX_PER_SHOP = 3          # nech sa rebríček nezaplní jedným eshopom
TOP_N = 30


def score_offer(product: dict, offer: dict) -> tuple[float, list[str]]:
    """Koľko bodov si ponuka zaslúži a prečo.

    Zámerne to nepočíta žiadny model — je to sčítanie štyroch vecí, ktoré si
    vieš overiť očami v tabuľke: ako hlboko je cena pod dnešným trhom, ako
    hlboko pod uvádzacou cenou, či je na historickom minime a či ide o edíciu,
    ktorá sa už netlačí. Tým pádom je výsledok stále rovnaký pre rovnaké dáta
    a dá sa mu veriť.
    """
    reasons: list[str] = []
    score = 0.0
    price = offer["price_eur"]

    median = product.get("median_eur")
    if median and price < median and product.get("median_trusted"):
        below = (median - price) / median
        score += min(below, UNDER_MARKET_MAX) / UNDER_MARKET_MAX * 40
        reasons.append(f"{round(below * 100)} % pod mediánom ponúk")

    launch = product.get("launch_eur")
    if launch and price < launch:
        below = (launch - price) / launch
        score += min(below, 0.25) / 0.25 * 25
        reasons.append(f"{round(below * 100)} % pod uvádzacou cenou")

    # Kým je história krátka, je "najnižšie doteraz" pravda skoro pri každom
    # produkte a tých 20 bodov nerozlišuje nič. Bonus preto začne platiť až
    # po dvoch týždňoch dát a dovtedy sa ani nezobrazuje.
    low = product.get("low_eur")
    history = product.get("history") or []
    if low and len(history) >= MIN_HISTORY_DAYS and price <= low * 1.03:
        score += 20
        reasons.append("na historickom minime")

    score += TIER_POINTS.get(product["edition"]["tier"], 3)

    if product.get("in_print") is False:
        score += 5
        reasons.append("po ukončení tlače")

    return round(score, 1), reasons


def build_recommendations(products: list[dict]) -> list[dict]:
    """Rebríček „čo dnes stojí za nákup“ — najlepšia ponuka na produkt."""
    candidates = []
    for product in products:
        best = None
        for offer in product["offers"]:
            # Ponuka označená `overiť` alebo `skok ceny` je skoro vždy chyba
            # eshopu alebo iný produkt; do odporúčaní nemá čo robiť.
            if not offer["in_stock"] or offer["outlier"]:
                continue
            if offer["flag"].startswith("overiť"):
                continue
            score, reasons = score_offer(product, offer)
            if best is None or score > best["score"]:
                best = {
                    "key": product["key"],
                    "title": product["title"],
                    "image": product["image"],
                    "tier": product["edition"]["tier"],
                    "series": product["edition"]["series"],
                    "format": product["format"]["short"],
                    "shop_id": offer["shop_id"],
                    "url": offer["url"],
                    "price_eur": offer["price_eur"],
                    "per_pack_eur": offer["per_pack_eur"],
                    "median_eur": product.get("median_eur"),
                    "low_eur": product.get("low_eur"),
                    "launch_eur": product.get("launch_eur"),
                    "in_print": product.get("in_print"),
                    "score": score,
                    "reasons": reasons,
                }
        if best and best["score"] > 0:
            candidates.append(best)

    candidates.sort(key=lambda c: (-c["score"], c["price_eur"]))
    per_shop: dict[str, int] = defaultdict(int)
    top = []
    for candidate in candidates:
        if per_shop[candidate["shop_id"]] >= MAX_PER_SHOP:
            continue
        per_shop[candidate["shop_id"]] += 1
        top.append(candidate)
        if len(top) >= TOP_N:
            break
    return top


# ------------------------------------------------------------------ cieľové ceny

def build_watchlist(products: list[dict], targets: list[dict]) -> dict:
    """Rozdelí sledované produkty na tie, čo cieľovú cenu splnili, a ostatné."""
    by_key = {p["key"]: p for p in products}
    met, waiting = [], []
    for target in targets or []:
        key = str(target.get("key") or "").strip()
        limit = float(target.get("target") or 0)
        if not key or limit <= 0:
            continue
        product = by_key.get(key)
        price = product.get("min_eur") if product else None
        cheapest = None
        if product and price is not None:
            cheapest = next((o for o in product["offers"]
                             if o["in_stock"] and o["price_eur"] == price), None)
        entry = {
            "key": key,
            "title": product["title"] if product else (target.get("title") or key),
            "target_eur": round(limit, 2),
            "price_eur": price,
            "shop_id": cheapest["shop_id"] if cheapest else "",
            "url": cheapest["url"] if cheapest else "",
            "gap_pct": (round((price - limit) / limit * 100, 1)
                        if price is not None else None),
            "found": bool(product),
        }
        if price is not None and price <= limit:
            met.append(entry)
        else:
            waiting.append(entry)
    met.sort(key=lambda e: e["gap_pct"] if e["gap_pct"] is not None else 0)
    waiting.sort(key=lambda e: (e["gap_pct"] is None,
                                e["gap_pct"] if e["gap_pct"] is not None else 0))
    return {"met": met, "waiting": waiting}


# ------------------------------------------------------------------ história portfólia

PORTFOLIO_FIELDS = ["date", "items", "cost_eur", "value_eur", "pl_eur", "pl_pct"]


def append_portfolio_history(portfolio: dict, today: str) -> list[dict]:
    """Zapíše dnešnú hodnotu portfólia a vráti posledný pol rok na graf.

    Rovnako ako pri cenách sa zápis z toho istého dňa prepisuje, nie pridáva —
    inak by opakovaný sken nakreslil do grafu tri body na jeden deň.
    """
    path = DATA / "portfolio-history.csv"
    rows: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8", newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("date") != today]
    totals = portfolio.get("totals") or {}
    if portfolio.get("items"):
        rows.append({
            "date": today,
            "items": len(portfolio["items"]),
            "cost_eur": totals.get("cost_eur") or 0,
            "value_eur": totals.get("value_eur") or 0,
            "pl_eur": totals.get("pl_eur") or 0,
            "pl_pct": totals.get("pl_pct") if totals.get("pl_pct") is not None else "",
        })
    rows.sort(key=lambda r: r["date"])
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PORTFOLIO_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in PORTFOLIO_FIELDS})

    points = []
    for row in rows[-180:]:
        try:
            points.append({"d": row["date"],
                           "cost": float(row["cost_eur"] or 0),
                           "value": float(row["value_eur"] or 0)})
        except (TypeError, ValueError):
            continue
    return points


# ------------------------------------------------------------------ upozornenia

ALERT_COOLDOWN_DAYS = 7    # to isté upozornenie nechodí každý večer znova
BIG_DROP_PCT = -10.0       # od koľkých percent stojí pokles za správu
SUSPICIOUS_DROP_PCT = -50.0  # pod tým to nebýva zľava, ale iný produkt v katalógu
ALERT_FIELDS = ["date", "key", "kind"]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def build_alerts(products: list[dict], movements: dict, watchlist: dict) -> list[dict]:
    """Tri veci stoja za vyrušenie: splnená cieľová cena, veľký prepad, naskladnenie
    edície úrovne A/B, ktorá nikde nebola."""
    tier = {p["key"]: p["edition"]["tier"] for p in products}
    alerts: list[dict] = []

    for item in watchlist.get("met", []):
        alerts.append({
            "key": item["key"], "kind": "target",
            "title": item["title"],
            "text": (f"cieľová cena {item['target_eur']:.2f} € splnená — "
                     f"{item['price_eur']:.2f} €"),
            "url": item["url"],
        })

    for item in movements.get("price_drop", []):
        # Polovičná cena zo dňa na deň nie je zľava — eshop skoro vždy len
        # prehodil, čo sa skrýva pod tou istou adresou. Nebudíme kvôli tomu.
        if (item.get("delta") is not None
                and SUSPICIOUS_DROP_PCT < item["delta"] <= BIG_DROP_PCT):
            alerts.append({
                "key": item["key"], "kind": "drop",
                "title": item["product"],
                "text": f"zlacnené o {abs(item['delta']):.1f} % na {item['price_eur']:.2f} €",
                "url": item["url"],
            })

    for item in movements.get("restocked", []):
        if tier.get(item["key"]) in ("A", "B"):
            alerts.append({
                "key": item["key"], "kind": "restock",
                "title": item["product"],
                "text": f"opäť skladom za {item['price_eur']:.2f} €",
                "url": item["url"],
            })

    seen = set()
    unique = []
    for alert in alerts:
        ident = (alert["key"], alert["kind"])
        if ident in seen:
            continue
        seen.add(ident)
        unique.append(alert)
    return unique


def read_alert_log() -> list[dict]:
    path = DATA / "alerts-sent.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def due_alerts(alerts: list[dict], log: list[dict], today: str) -> list[dict]:
    """Vyhodí tie, ktoré už išli v posledných dňoch. Čistá funkcia — testovateľná."""
    try:
        now = date.fromisoformat(today)
    except ValueError:
        return alerts
    recent = set()
    for row in log:
        try:
            when = date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            continue
        if (now - when).days < ALERT_COOLDOWN_DAYS:
            recent.add((row.get("key", ""), row.get("kind", "")))
    return [a for a in alerts if (a["key"], a["kind"]) not in recent]


def write_alert_log(alerts: list[dict], today: str) -> None:
    path = DATA / "alerts-sent.csv"
    exists = path.exists()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ALERT_FIELDS)
        if not exists:
            writer.writeheader()
        for alert in alerts:
            writer.writerow({"date": today, "key": alert["key"], "kind": alert["kind"]})


async def send_alerts(alerts: list[dict], today: str) -> int:
    """Pošle upozornenia na Telegram. Bez nastavených premenných nerobí nič
    a nikdy nezhodí sken — upozornenie je doplnok, nie účel behu."""
    if not alerts:
        return 0
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print(f"Upozornení na poslanie: {len(alerts)} (Telegram nie je nastavený)")
        return 0
    pending = due_alerts(alerts, read_alert_log(), today)
    if not pending:
        return 0
    lines = ["*Cenová mapa Pokémon TCG*"]
    marks = {"target": "🎯", "drop": "📉", "restock": "📦"}
    for alert in pending[:20]:
        title = alert["title"].replace("*", "").replace("_", "")
        lines.append(f"{marks.get(alert['kind'], '•')} [{title}]({alert['url']}) — {alert['text']}")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": "\n".join(lines),
                      "parse_mode": "Markdown", "disable_web_page_preview": True})
            resp.raise_for_status()
    except Exception as exc:                                   # noqa: BLE001
        print(f"Telegram: {type(exc).__name__} — upozornenia sa neposlali")
        return 0
    write_alert_log(pending, today)
    print(f"Poslaných {len(pending)} upozornení na Telegram")
    return len(pending)


# ------------------------------------------------------------------ main

async def run(args) -> int:
    today = date.today().isoformat()
    shops_cfg = load_yaml("shops.yaml")
    defaults = shops_cfg.get("defaults", {})
    shops = shops_cfg["shops"]
    if args.only:
        wanted = set(args.only.split(","))
        shops = [s for s in shops if s["id"] in wanted]

    images = (load_yaml("images.yaml") or {}).get("images", {}) or {}
    history = read_history()

    fx = await fetch_fx()
    snapshots = Path(args.snapshots) if args.snapshots else None
    results = await fetch_all(shops, defaults, snapshots)
    rows, unknown = build_rows(results, fx, today)

    fatal, warnings = check_health(results, history, rows)

    for result in results:
        shop = result["shop"]
        status = "ok " if result["offers"] else "PRÁZDNE"
        print(f"{status} {shop['name']:16} {len(result['offers']):3} položiek", end="")
        if result["errors"]:
            print(f"  ({len(result['errors'])} chýb: {result['errors'][0][:90]})", end="")
        print()

    kept = len(rows)
    proxied = [s_["id"] for s_ in shops if s_.get("proxy")]
    if proxied:
        state = "cez proxy" if PROXY_URL else "BEZ proxy (premenné nie sú nastavené)"
        print(f"Označené na proxy ({len(proxied)}): {state}")
    print(f"\nZaradených {kept} ponúk, {len(unknown)} nerozpoznaných názvov, "
          f"kurz CZK/EUR {1 / fx['czk_eur']:.2f}{' (starý)' if fx.get('stale') else ''}")

    for warning in warnings:
        print(f"POZOR: {warning}")

    if kept == 0:
        fatal.append("žiadne zaradené položky")

    if fatal:
        for problem in fatal:
            print(f"CHYBA: {problem}", file=sys.stderr)
        if not args.force:
            print("\nDáta sa nezapisujú. Skontroluj adaptéry alebo spusti s --force.",
                  file=sys.stderr)
            return 2
        print("Pokračujem napriek chybám (--force).")

    products, movements = build_products(rows, history, images, today)
    portfolio_config = load_yaml("portfolio.yaml") or {}
    remote = await fetch_from_worker("portfolio", "holdings")
    if remote:
        portfolio_config = dict(portfolio_config)
        portfolio_config["holdings"] = (portfolio_config.get("holdings") or []) + remote
        print(f"Portfólio zo stránky: {len(remote)} položiek")
    portfolio = build_portfolio(products, portfolio_config, fx)
    if portfolio["items"]:
        totals = portfolio["totals"]
        print(f"Portfólio: {len(portfolio['items'])} položiek, náklady "
              f"{totals['cost_eur']} €, hodnota {totals['value_eur']} €, "
              f"rozdiel {totals['pl_eur']} €")

    targets = await fetch_from_worker("watchlist", "watchlist")
    targets += (load_yaml("portfolio.yaml") or {}).get("targets") or []
    watchlist = build_watchlist(products, targets)
    recommendations = build_recommendations(products)
    if watchlist["met"]:
        print(f"Cieľová cena splnená pri {len(watchlist['met'])} produktoch")

    portfolio_history = []
    if not args.dry_run:
        append_history(rows)
        portfolio_history = append_portfolio_history(portfolio, today)
        if unknown:
            path = DATA / "unknown.csv"
            seen = set()
            if path.exists():
                with open(path, encoding="utf-8", newline="") as fh:
                    seen = {r["name"] for r in csv.DictReader(fh)}
            fresh, batch = [], set()
            for item in unknown:                      # zapisuj každý názov len raz
                if item["name"] in seen or item["name"] in batch:
                    continue
                batch.add(item["name"])
                fresh.append(item)
            if fresh:
                exists = path.exists()
                with open(path, "a", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=["date", "shop_id", "name", "url"])
                    if not exists:
                        writer.writeheader()
                    writer.writerows(fresh)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "fx": {"czk_per_eur": round(1 / fx["czk_eur"], 3),
               "date": fx.get("date"), "stale": fx.get("stale", False)},
        "shops": [{
            "id": r["shop"]["id"], "name": r["shop"]["name"],
            "country": r["shop"]["country"],
            "ok": bool(r["offers"]), "optional": bool(r["shop"].get("optional")),
            "count": len(r["offers"]),
            "error": (r["errors"][0][:200] if r["errors"] else ""),
        } for r in results],
        "products": products,
        "movements": movements,
        "portfolio": portfolio,
        "portfolio_history": portfolio_history,
        "watchlist": watchlist,
        "recommendations": recommendations,
        "portfolio_endpoint": PROXY_URL,
        "counts": {"offers": kept, "products": len(products),
                   "unknown": len(unknown),
                   "min_sellers_for_median": MIN_FOR_MEDIAN},
    }
    (DATA / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    DOCS.mkdir(exist_ok=True)
    (DOCS / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Zapísaných {len(products)} produktov do latest.json")

    if not args.dry_run:
        await send_alerts(build_alerts(products, movements, watchlist), today)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Denný sken cien Pokémon TCG")
    parser.add_argument("--dry-run", action="store_true", help="nezapisovať históriu")
    parser.add_argument("--force", action="store_true", help="ignorovať poistky")
    parser.add_argument("--only", help="čiarkou oddelené id eshopov")
    parser.add_argument("--snapshots", help="adresár na uloženie stiahnutých stránok")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
