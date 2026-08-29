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
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

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
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sk-SK,sk;q=0.9,cs;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

DROP_RATIO = 0.5          # menej než polovica položiek oproti minule = zlyhanie
OUTLIER_LOW = 0.3         # cena pod 30 % predchádzajúcej = podozrivá
OUTLIER_HIGH = 3.0
UNDER_MARKET_MIN = 0.05   # 5 % pod mediánom = zaujímavé
UNDER_MARKET_MAX = 0.30   # nad 30 % = skôr chyba eshopu než príležitosť


# ------------------------------------------------------------------ config

def load_yaml(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ------------------------------------------------------------------ fetch

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
                resp = await client.get(url)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                body = resp.text
                save_snapshot(snapshots, shop["id"], page, body)
                found = adapters.parse(shop["adapter"], body, shop)
                offers.extend(found)
                if not found:
                    break          # prázdna strana = koniec stránkovania
                url = adapters.next_page(shop["adapter"], body, str(resp.url))
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
                resp = await client.get(url)
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
    timeout = httpx.Timeout(25.0, connect=15.0)
    sem = asyncio.Semaphore(defaults.get("concurrency", 3))

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True,
                                 limits=limits, timeout=timeout, http2=False) as client:
        async def run(shop):
            async with sem:
                worker = fetch_shopify if shop["adapter"] == "shopify" else fetch_shop
                result = await worker(client, shop, defaults, snapshots)
                await asyncio.sleep(0.8)          # nezaťažovať eshop
                return result

        return await asyncio.gather(*(run(s) for s in shops))


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
    "date", "shop_id", "edition_id", "format_id", "packs", "name", "url",
    "price", "currency", "price_eur", "per_pack_eur", "in_stock", "image",
]


def append_history(rows: list[dict]) -> None:
    path = DATA / "history.csv"
    exists = path.exists()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if not exists:
            writer.writeheader()
        for row in rows:
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
                "packs": hit.packs,
                "name": offer.name,
                "url": offer.url,
                "price": round(offer.price, 2),
                "currency": offer.currency.upper(),
                "price_eur": price_eur,
                "per_pack_eur": round(price_eur / hit.packs, 2),
                "in_stock": "1" if offer.in_stock else "0",
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
        key = (row["shop_id"], row["edition_id"], row["format_id"])
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
    snapshot = {(r["shop_id"], r["edition_id"], r["format_id"]): r
                for r in history if r["date"] == last_date}
    return last_date, snapshot


def guard_counts(results: list[dict], history: list[dict], today: str) -> list[str]:
    """Poistka: eshop, ktorý zrazu vracia polovicu položiek, sa neignoruje."""
    problems = []
    previous = defaultdict(int)
    last_date, _ = previous_snapshot(history)
    for row in history:
        if row["date"] == last_date:
            previous[row["shop_id"]] += 1
    for result in results:
        shop = result["shop"]
        before = previous.get(shop["id"], 0)
        now = len(result["offers"])
        if before >= 4 and now < before * DROP_RATIO:
            problems.append(
                f"{shop['name']}: {now} položiek oproti {before} v poslednom behu")
    return problems


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
        grouped[(row["edition_id"], row["format_id"])].append(row)

    # denné minimum skladom na produkt, pre sparkliny
    series = defaultdict(dict)
    for row in history + rows:
        if row.get("in_stock") not in ("1", 1, True):
            continue
        key = (row["edition_id"], row["format_id"])
        day = row["date"]
        value = float(row["price_eur"])
        current = series[key].get(day)
        series[key][day] = value if current is None else min(current, value)

    products, movements = [], {"restocked": [], "sold_out": [], "price_drop": [], "new": []}

    for (edition_id, format_id), offers in sorted(grouped.items()):
        edition = classify.edition_by_id(edition_id)
        fmt = classify.format_by_id(format_id)
        if edition is None or fmt is None:
            continue
        in_stock = [o for o in offers if o["in_stock"] == "1"]
        prices_in_stock = sorted(float(o["price_eur"]) for o in in_stock)
        median_eur = statistics.median(prices_in_stock) if prices_in_stock else None
        min_eur = prices_in_stock[0] if prices_in_stock else None

        key = f"{edition_id}|{format_id}"
        image = images.get(key) or next((o["image"] for o in offers if o["image"]), "")

        offer_list = []
        for offer in sorted(offers, key=lambda o: float(o["price_eur"])):
            price_eur = float(offer["price_eur"])
            prev = previous.get((offer["shop_id"], edition_id, format_id))
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
                        "key": key, "product": f"{edition.name} — {fmt.name}",
                        "shop_id": offer["shop_id"], "price_eur": price_eur,
                        "url": offer["url"],
                    })
                if delta is not None and delta <= -3 and offer["in_stock"] == "1":
                    movements["price_drop"].append({
                        "key": key, "product": f"{edition.name} — {fmt.name}",
                        "shop_id": offer["shop_id"], "price_eur": price_eur,
                        "delta": delta, "url": offer["url"],
                    })
            elif last_date:
                movements["new"].append({
                    "key": key, "product": f"{edition.name} — {fmt.name}",
                    "shop_id": offer["shop_id"], "price_eur": price_eur,
                    "url": offer["url"],
                })
            offer_list.append({
                "shop_id": offer["shop_id"],
                "price": float(offer["price"]),
                "currency": offer["currency"],
                "price_eur": price_eur,
                "per_pack_eur": float(offer["per_pack_eur"]),
                "in_stock": offer["in_stock"] == "1",
                "url": offer["url"],
                "name": offer["name"],
                "delta_pct": delta,
                "flag": flag_offer(price_eur, median_eur) if offer["in_stock"] == "1" else "",
                "outlier": bool(offer.get("outlier")),
            })

        points = sorted(series[(edition_id, format_id)].items())[-60:]
        min_any = min(float(o["price_eur"]) for o in offers)
        min_delta = None
        if min_eur is not None and len(points) >= 2 and points[-1][0] == today:
            before = points[-2][1]
            if before > 0:
                min_delta = round((min_eur - before) / before * 100, 1)
        products.append({
            "key": key,
            "edition": {"id": edition.id, "name": edition.name,
                        "code": edition.code, "tier": edition.tier},
            "format": {"id": fmt.id, "name": fmt.name, "short": fmt.short},
            "packs": offers[0]["packs"],
            "image": image,
            "min_eur": min_eur,
            "min_any_eur": round(min_any, 2),
            "min_delta_pct": min_delta,
            "median_eur": round(median_eur, 2) if median_eur else None,
            "min_per_pack_eur": round(min_eur / offers[0]["packs"], 2) if min_eur else None,
            "offer_count": len(offers),
            "in_stock_count": len(in_stock),
            "history": [{"d": d, "v": v} for d, v in points],
            "offers": offer_list,
        })

    order = {"A": 0, "B": 1, "C": 2}
    products.sort(key=lambda p: (order.get(p["edition"]["tier"], 9),
                                 p["edition"]["name"], p["format"]["id"]))
    return products, movements


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

    failures = [r for r in results if not r["offers"]]
    hard_failures = [r for r in failures if not r["shop"].get("optional")]
    drops = guard_counts(results, history, today)

    for result in results:
        shop = result["shop"]
        status = "ok " if result["offers"] else "PRÁZDNE"
        print(f"{status} {shop['name']:16} {len(result['offers']):3} položiek", end="")
        if result["errors"]:
            print(f"  ({len(result['errors'])} chýb: {result['errors'][0][:90]})", end="")
        print()

    kept = len(rows)
    print(f"\nZaradených {kept} ponúk, {len(unknown)} nerozpoznaných názvov, "
          f"kurz CZK/EUR {1 / fx['czk_eur']:.2f}{' (starý)' if fx.get('stale') else ''}")

    if hard_failures or drops:
        for result in hard_failures:
            print(f"CHYBA: {result['shop']['name']} nevrátil žiadne položky", file=sys.stderr)
        for problem in drops:
            print(f"CHYBA: prepad počtu položiek — {problem}", file=sys.stderr)
        if not args.force:
            print("\nDáta sa nezapisujú. Skontroluj adaptér alebo spusti s --force.",
                  file=sys.stderr)
            return 2

    if kept == 0:
        print("CHYBA: žiadne zaradené položky", file=sys.stderr)
        return 2

    products, movements = build_products(rows, history, images, today)

    if not args.dry_run:
        append_history(rows)
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
        "counts": {"offers": kept, "products": len(products),
                   "unknown": len(unknown)},
    }
    (DATA / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    DOCS.mkdir(exist_ok=True)
    (DOCS / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Zapísaných {len(products)} produktov do latest.json")
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
