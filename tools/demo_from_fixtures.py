"""Postaví latest.json zo snapshotov v tests/fixtures — bez siete.

Slúži na náhľad stránky a na rýchlu kontrolu agregácie po zmene kódu.
Nezapisuje do histórie.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import adapters      # noqa: E402
import scrape        # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
SHOPS = {
    "cardstore": ("shoptet", "cardstore-cz", "Cardstore.cz", "CZ", "CZK", "https://www.cardstore.cz"),
    "zardo": ("upgates", "zardo-cz", "Zardo Cards", "CZ", "EUR", "https://www.zardo.cards"),
    "pompo": ("pompo", "pompo-cz", "Pompo.cz", "CZ", "CZK", "https://pompo.cz"),
    "digihry": ("digihry", "digihry-sk", "Digihry.sk", "SK", "EUR", "https://www.digihry.sk"),
    "pgs": ("pgs", "pgs-sk", "PGS.sk", "SK", "EUR", "https://www.pgs.sk"),
    "veselydrak": ("veselydrak", "vesely-drak-cz", "Veselý drak CZ", "CZ", "CZK", "https://www.vesely-drak.cz"),
    "alza": ("alza", "alza-sk", "Alza.sk", "SK", "EUR", "https://www.alza.sk"),
    "cardyx": ("shopify", "cardyx-sk", "Cardyx.sk", "SK", "EUR", "https://www.cardyx.sk"),
    "pokectcg": ("woocommerce", "pokectcg-cz", "PokecTCG.cz", "CZ", "CZK", "https://pokectcg.cz"),
    "xzone": ("xzone", "xzone-sk", "Xzone.sk", "SK", "EUR", "https://www.xzone.sk"),
    "geekhall": ("woocommerce", "geekhall-cz", "GeekHall.cz", "CZ", "CZK", "https://geekhall.cz"),
    "dazzle": ("opencart", "dazzle-sk", "Dazzle.sk", "SK", "EUR", "https://www.dazzle.sk"),
}


def read(name: str) -> str:
    for suffix in (".html.gz", ".json.gz"):
        path = FIXTURES / f"{name}{suffix}"
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return fh.read()
    raise FileNotFoundError(name)


def main() -> int:
    fx = {"czk_eur": 1 / 24.1, "date": str(date.today()), "stale": True}
    today = date.today().isoformat()
    results = []
    for key, (adapter, shop_id, name, country, currency, base) in SHOPS.items():
        shop = {"id": shop_id, "name": name, "country": country,
                "currency": currency, "base": base, "adapter": adapter}
        offers = adapters.parse(adapter, read(key), shop)
        results.append({"shop": shop, "offers": offers, "errors": []})

    rows, unknown = scrape.build_rows(results, fx, today)

    # umelá história, aby bolo vidieť sparkliny a graf
    history = []
    for back in range(14, 0, -1):
        day = (date.today() - timedelta(days=back)).isoformat()
        for i, row in enumerate(rows):
            drift = 1 + ((i % 5) - 2) * 0.004 * back
            history.append({**row, "date": day,
                            "price_eur": round(float(row["price_eur"]) * drift, 2)})

    products, movements = scrape.build_products(rows, history, {}, today)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "fx": {"czk_per_eur": round(1 / fx["czk_eur"], 3), "date": fx["date"], "stale": True},
        "shops": [{"id": r["shop"]["id"], "name": r["shop"]["name"],
                   "country": r["shop"]["country"], "ok": True, "optional": False,
                   "count": len(r["offers"]), "error": ""} for r in results],
        "products": products,
        "movements": movements,
        "counts": {"offers": len(rows), "products": len(products), "unknown": len(unknown)},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (ROOT / "docs" / "latest.json").write_text(body, encoding="utf-8")
    print(f"Demo: {len(rows)} ponúk, {len(products)} produktov, "
          f"{len(unknown)} nerozpoznaných -> docs/latest.json")
    for p in products[:12]:
        print(f"  {p['edition']['name'][:22]:24}{p['format']['short']:8}"
              f"{p['offer_count']:3} ponúk  min {p['min_eur']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
