"""Testy parsovacej logiky nad uloženými snapshotmi stránok.

Zámerne nesiahajú na živé weby: keď test spadne, je chyba v kóde, nie na
internete. Snapshoty v tests/fixtures/ sa obnovujú ručne pri zmene eshopu.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

import adapters
import classify

FIXTURES = Path(__file__).parent / "fixtures"

SHOPS = {
    "cardstore": {"id": "cardstore-cz", "base": "https://www.cardstore.cz", "currency": "CZK"},
    "zardo": {"id": "zardo-cz", "base": "https://www.zardo.cards", "currency": "EUR"},
    "pompo": {"id": "pompo-cz", "base": "https://pompo.cz", "currency": "CZK"},
    "digihry": {"id": "digihry-sk", "base": "https://www.digihry.sk", "currency": "EUR"},
    "pgs": {"id": "pgs-sk", "base": "https://www.pgs.sk", "currency": "EUR"},
    "veselydrak": {"id": "vesely-drak-cz", "base": "https://www.vesely-drak.cz", "currency": "CZK"},
    "alza": {"id": "alza-sk", "base": "https://www.alza.sk", "currency": "EUR"},
    "cardyx": {"id": "cardyx-sk", "base": "https://www.cardyx.sk", "currency": "EUR"},
    "pokectcg": {"id": "pokectcg-cz", "base": "https://pokectcg.cz", "currency": "CZK"},
    "xzone": {"id": "xzone-sk", "base": "https://www.xzone.sk", "currency": "EUR"},
    "geekhall": {"id": "geekhall-cz", "base": "https://geekhall.cz", "currency": "CZK"},
    "dazzle": {"id": "dazzle-sk", "base": "https://www.dazzle.sk", "currency": "EUR"},
}

ADAPTER_OF = {
    "cardstore": "shoptet", "zardo": "upgates", "pompo": "pompo", "digihry": "digihry",
    "pgs": "pgs", "veselydrak": "veselydrak", "alza": "alza", "cardyx": "shopify",
    "pokectcg": "woocommerce", "xzone": "xzone", "geekhall": "woocommerce",
    "dazzle": "opencart",
}

MIN_OFFERS = {
    "cardstore": 10, "zardo": 8, "pompo": 20, "digihry": 30,
    "pgs": 20, "veselydrak": 18, "alza": 20, "cardyx": 25,
    "pokectcg": 40, "xzone": 20, "geekhall": 10, "dazzle": 18,
}


def load(name: str) -> str:
    path = FIXTURES / f"{name}.html.gz"
    if not path.exists():
        path = FIXTURES / f"{name}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return fh.read()


def offers_for(name: str):
    return adapters.parse(ADAPTER_OF[name], load(name), SHOPS[name])


# ------------------------------------------------------------- adaptéry

@pytest.mark.parametrize("name", sorted(ADAPTER_OF))
def test_adapter_returns_offers(name):
    offers = offers_for(name)
    assert len(offers) >= MIN_OFFERS[name], f"{name}: len {len(offers)} položiek"


@pytest.mark.parametrize("name", sorted(ADAPTER_OF))
def test_offers_have_required_fields(name):
    for offer in offers_for(name):
        assert offer.name.strip(), f"{name}: prázdny názov"
        assert offer.price and offer.price > 0, f"{name}: {offer.name} bez ceny"
        assert offer.url.startswith("http"), f"{name}: {offer.name} má zlú URL {offer.url!r}"
        assert offer.currency in ("CZK", "EUR")


@pytest.mark.parametrize("name", sorted(ADAPTER_OF))
def test_stock_is_resolved(name):
    """Neznámy stav skladu je horší než zlý — vždy chceme True/False."""
    offers = offers_for(name)
    unknown = [o for o in offers if o.in_stock is None]
    assert len(unknown) <= len(offers) * 0.2, \
        f"{name}: {len(unknown)} z {len(offers)} ponúk bez stavu skladu"


@pytest.mark.parametrize("name", sorted(ADAPTER_OF))
def test_images_present(name):
    offers = offers_for(name)
    with_image = [o for o in offers if o.image.startswith("http")]
    assert len(with_image) >= len(offers) * 0.7, f"{name}: málo obrázkov"


# GeekHall má na prvej strane kategórie len blistre, tiny a plagáty; sledované
# formáty sú až na ďalších stranách. Snapshot je preto legitímne bez zhody.
NO_TRACKED_ON_PAGE_ONE = {"geekhall"}


@pytest.mark.parametrize("name", sorted(set(ADAPTER_OF) - NO_TRACKED_ON_PAGE_ONE))
def test_at_least_one_tracked_product(name):
    kept = [o for o in offers_for(name) if classify.classify(o.name)]
    assert kept, f"{name}: ani jeden sledovaný produkt"


@pytest.mark.parametrize("name", sorted(NO_TRACKED_ON_PAGE_ONE))
def test_classifier_survives_untracked_pages(name):
    """Aj strana bez jediného sledovaného produktu musí prejsť bez výnimky."""
    for offer in offers_for(name):
        classify.classify(offer.name)


def test_shoptet_known_offer():
    offers = {o.name: o for o in offers_for("cardstore")}
    box = next(o for n, o in offers.items() if "Pitch Black" in n and "Booster Box" in n)
    assert box.currency == "CZK"
    assert 3000 < box.price < 7000
    assert box.in_stock is False


def test_pgs_known_offer():
    offers = {o.name: o for o in offers_for("pgs")}
    booster = next(o for n, o in offers.items() if "Pitch Black" in n and "Booster" in n)
    assert booster.price == pytest.approx(5.99)
    assert booster.in_stock is True


def test_shopify_availability_flag_wins():
    """Cardyx má v šablóne fixný text — stav sa musí čítať z 'available'."""
    offers = {o.name: o for o in offers_for("cardyx")}
    assert any(o.in_stock for o in offers.values())
    assert any(o.in_stock is False for o in offers.values())


# ------------------------------------------------------------- stránkovanie

@pytest.mark.parametrize("name,expected", [
    ("cardstore", "strana-2"),
    ("pompo", "page=2"),
    ("digihry", "/18"),
    ("veselydrak", "page=2"),
    ("alza", "p2.htm"),
])
def test_pagination_detected(name, expected):
    url = adapters.next_page(ADAPTER_OF[name], load(name), SHOPS[name]["base"] + "/")
    assert url and expected in url, f"{name}: next_page = {url!r}"


def test_xzone_pagination_is_blind():
    """Xzone nemá v HTML odkaz na ďalšiu stranu — ideme naslepo a scrape.py
    zastaví, keď strana nevráti ani jednu položku."""
    url = adapters.next_page("xzone", load("xzone"), "https://www.xzone.sk/pokemon")
    assert url == "https://www.xzone.sk/pokemon?s=60&page=2"


def test_opencart_does_not_double_count():
    """dazzle.sk vykreslí každý produkt v zozname aj v mriežke — adaptér smie
    čítať len jednu z tých dvoch podôb."""
    offers = offers_for("dazzle")
    assert len(offers) == 20, f"{len(offers)} položiek namiesto 20"
    assert len({o.url for o in offers}) == len(offers), "duplicitné URL"


def test_opencart_preorder_is_not_in_stock():
    """Predobjednávka sa na dazzle.sk tvári ako „Skladom > 5 ks“ — rozhodnúť
    musí stužka PREDOBJEDNÁVKA, inak by monitor hlásil dostupnosť, ktorá nie je."""
    offers = {o.name: o for o in offers_for("dazzle")}
    preorder = next(o for n, o in offers.items() if "30th Celebration" in n)
    assert preorder.in_stock is False
    assert "predobjedn" in preorder.stock_text.lower()
    assert any(o.in_stock for o in offers.values()), "nič nie je skladom?"


def test_woocommerce_oxygen_variant():
    """GeekHall beží na WooCommerce, ale šablóna z Oxygen Builderu negeneruje
    li.product — adaptér musí chytiť aj article.product_card."""
    offers = offers_for("geekhall")
    assert len(offers) == 12, f"{len(offers)} položiek namiesto 12"
    assert all(o.in_stock is not None for o in offers)
    assert all(o.image.startswith("http") for o in offers)


def test_woocommerce_stock_from_css_class():
    offers = offers_for("pokectcg")
    assert any(o.in_stock for o in offers)
    assert any(o.in_stock is False for o in offers)


def test_shopify_has_no_html_pagination():
    assert adapters.next_page("shopify", load("cardyx"), "https://www.cardyx.sk/") is None


# ------------------------------------------------------------- ceny a sklad

@pytest.mark.parametrize("text,expected", [
    ("1 399 Kč", 1399.0),
    ("5,99 €", 5.99),
    ("3399.00", 3399.0),
    ("1 286,27 €", 1286.27),
    ("30 999 Kč", 30999.0),
    ("", None),
    ("0 €", None),
])
def test_parse_price(text, expected):
    assert adapters.parse_price(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Skladem > 10 ks", True),
    ("Na sklade > 5 ks", True),
    ("In stock", True),
    ("Vyprodáno", False),
    ("Vypredané", False),
    ("Predobjednávka", False),
    ("Na objednávku", False),
    ("", None),
])
def test_stock_from_text(text, expected):
    assert adapters.stock_from_text(text) is expected


# ------------------------------------------------------------- klasifikácia

@pytest.mark.parametrize("name,edition,fmt,packs", [
    ("Pokémon TCG: ME04 Chaos Rising Booster", "chaos-rising", "booster", 1),
    ("Pokémon TCG: SV8.5 Prismatic Evolutions Booster Bundle", "prismatic-evolutions", "bundle", 6),
    ("Pokémon TCG: Pitch Black - Booster Box", "pitch-black", "booster-box", 36),
    ("Pokémon TCG: ME03 Perfect Order Elite Trainer Box", "perfect-order", "etb", 9),
    ("Pokémon TCG: 30th Celebration Elite Trainer Box", "30th-celebration", "etb", 10),
    ("Pokemon TCG 30. výročie Booster Bundle", "30th-celebration", "bundle", 6),
    # rozšírenie na celú sériu SV a ME
    ("Pokémon TCG ME01 Mega Evolutions Booster Box", "mega-evolution", "booster-box", 36),
    ("Pokémon TCG: SV10 Destined Rivals - Booster box", "destined-rivals", "booster-box", 36),
    ("Pokémon TCG: SV6.5 - Shrouded fable - Booster Bundle", "shrouded-fable", "bundle", 6),
    ("Pokémon TCG: Temporal Forces SV05 Half Booster Box", "temporal-forces", "half-box", 18),
    ("Pokémon TCG: SV07 Stellar Crown - 3 Blister Booster", "stellar-crown", "blister-3", 3),
    ("Pokémon TCG: Scarlet & Violet 151 - Booster Bundle", "pokemon-151", "bundle", 6),
    ("Pokémon TCG: ME02.5 Ascended Heroes Mini Tin", "ascended-heroes", "mini-tin", 2),
    ("Pokémon TCG: SV8.5 Prismatic Evolutions Booster Bundle Display",
     "prismatic-evolutions", "bundle-display", 48),
])
def test_classify_hits(name, edition, fmt, packs):
    hit = classify.classify(name)
    assert hit is not None, name
    assert hit.edition.id == edition
    assert hit.format.id == fmt
    assert hit.packs == packs


@pytest.mark.parametrize("name", [
    "Pokémon TCG: Storm Emeralda Booster Box - japonský",
    "Pokémon TCG: Pitch Black Booster Box Case (6x Booster Box)",
    "Pokémon TCG: Lost Origin Booster Box Sword and Shield 11",   # mimo SV a ME
    "Spin Master Bitzee Bouda pro pejsky",
    "Album Ultimate Guard - Flexxfolio 360 18-vreckový",
    "Ampharos - 090/086 - ME04: Chaos Rising (CRI)",              # jednotlivá karta
])
def test_classify_rejects(name):
    assert classify.classify(name) is None, name


@pytest.mark.parametrize("name,variant", [
    ("Pokémon TCG: Mega Charizard X ex Ultra Premium Collection (2025)", "mega-charizard-ex"),
    ("Pokémon TCG Mega Charizard X ex - Ultra Premium Collection", "mega-charizard-ex"),
    ("Pokémon TCG: Terapagos EX Ultra Premium Collection", "terapagos-ex"),
])
def test_standalone_premium_collections(name, variant):
    """Premiové kolekcie sa predávajú bez kódu setu. Nesmú vypadnúť — a zároveň
    sa nesmú zliať do jednej položky, preto ich rozlišuje `variant`."""
    hit = classify.classify(name)
    assert hit is not None, name
    assert hit.edition.id == "standalone"
    assert hit.format.id == "ultra-premium"
    assert hit.variant == variant


def test_formats_without_pack_count():
    """UPC a podobné kolekcie majú premenlivý počet balíčkov — radšej žiadny
    údaj než vymyslený, inak by cena za balíček klamala."""
    hit = classify.classify("Pokémon TCG: Terapagos EX Ultra Premium Collection")
    assert hit.packs is None


def test_30th_etb_has_ten_packs():
    """Výnimka: 30th Celebration ETB má 10 balíčkov, nie 9."""
    other = classify.classify("Pokémon TCG: Chaos Rising Elite Trainer Box")
    special = classify.classify("Pokémon TCG: 30th Celebration Elite Trainer Box")
    assert other.packs == 9 and special.packs == 10


def test_tiers_are_valid_and_series_complete():
    """Tier je nepovinný — sledujeme celú sériu, nie len investičné špičky."""
    assert {e.tier for e in classify.editions()} <= {"A", "B", "C", ""}
    assert any(e.tier == "A" for e in classify.editions())
    series = [e.series for e in classify.editions()]
    assert series.count("ME") >= 7, "chýbajú sety Mega Evolution"
    assert series.count("SV") >= 15, "chýbajú sety Scarlet & Violet"


def test_half_sets_win_over_base_sets():
    """SV8.5 sa nesmie chytiť na vzor pre SV08 — polovičné sety musia
    byť v konfigurácii nad základnými."""
    assert classify.classify("Pokémon TCG: SV8.5 Prismatic Evolutions - Booster") \
        .edition.id == "prismatic-evolutions"
    assert classify.classify("Pokémon TCG: SV08 Surging Sparks - Booster") \
        .edition.id == "surging-sparks"
    assert classify.classify("Pokémon TCG: ME02.5 Ascended Heroes - Booster") \
        .edition.id == "ascended-heroes"
    assert classify.classify("Pokémon TCG: ME02 Phantasmal Flames - Booster") \
        .edition.id == "phantasmal-flames"


def test_twin_sets_are_separate_products():
    """White Flare a Black Bolt zdieľajú kód SV10.5, ale sú to iné sety."""
    white = classify.classify("Pokémon TCG: SV10.5 White Flare - Elite Trainer Box")
    black = classify.classify("Pokémon TCG: SV10.5 Black Bolt - Elite Trainer Box")
    assert white.edition.id != black.edition.id
    assert white.edition.code == black.edition.code == "SV10.5"


# ------------------------------------------------------------- konfigurácia

def test_shops_config_is_sane():
    """Preklep v shops.yaml sa nemá prejaviť až pri nočnom behu."""
    import yaml
    from pathlib import Path
    from urllib.parse import urlparse

    config = yaml.safe_load((Path(__file__).parent.parent / "config" / "shops.yaml")
                            .read_text(encoding="utf-8"))
    shops = config["shops"]
    ids = [s["id"] for s in shops]
    assert len(ids) == len(set(ids)), "duplicitné id eshopu"

    known = set(adapters.PARSERS) | {"shopify"}
    for shop in shops:
        assert shop["adapter"] in known, f"{shop['id']}: neznámy adaptér {shop['adapter']}"
        assert shop["currency"] in ("CZK", "EUR"), shop["id"]
        assert shop["country"] in ("CZ", "SK"), shop["id"]
        assert shop["urls"], f"{shop['id']}: žiadne vstupné URL"
        base_host = urlparse(shop["base"]).netloc.replace("www.", "")
        for url in shop["urls"]:
            host = urlparse(url).netloc.replace("www.", "")
            assert host == base_host, f"{shop['id']}: {url} nesedí s base {shop['base']}"


def test_every_adapter_is_used_by_some_shop():
    import yaml
    from pathlib import Path
    config = yaml.safe_load((Path(__file__).parent.parent / "config" / "shops.yaml")
                            .read_text(encoding="utf-8"))
    used = {s["adapter"] for s in config["shops"]}
    assert used == set(adapters.PARSERS) | {"shopify"}, \
        f"nepoužité adaptéry: {(set(adapters.PARSERS) | {'shopify'}) - used}"


def test_every_adapter_has_a_fixture():
    assert set(ADAPTER_OF.values()) == set(adapters.PARSERS) | {"shopify"}


# ------------------------------------------------------------- poistky behu

def _result(shop_id, count, optional=False, error=""):
    offer = adapters.Offer(shop_id=shop_id, name="x", url="https://x/", price=1.0,
                           currency="EUR", in_stock=True)
    return {"shop": {"id": shop_id, "name": shop_id, "optional": optional},
            "offers": [offer] * count, "errors": [error] if error else []}


def _rows(shop_id, count, date="2026-08-30"):
    return [{"date": date, "shop_id": shop_id, "edition_id": "chaos-rising",
             "format_id": "bundle", "packs": 6, "name": "x", "url": "https://x/",
             "price": 40, "currency": "EUR", "price_eur": 40, "per_pack_eur": 6.7,
             "in_stock": "1", "image": ""} for _ in range(count)]


def test_health_single_shop_outage_is_only_a_warning():
    """Pri 26 eshopoch je výpadok jedného normálna prevádzka."""
    import scrape
    results = [_result(f"shop{i}", 5) for i in range(17)] + [_result("shop17", 0)]
    rows = sum([_rows(f"shop{i}", 5) for i in range(17)], [])
    fatal, warnings = scrape.check_health(results, [], rows)
    assert not fatal
    assert any("shop17" in w for w in warnings)


def test_health_fails_when_most_required_shops_are_down():
    import scrape
    results = [_result(f"shop{i}", 5) for i in range(10)] + \
              [_result(f"dead{i}", 0) for i in range(8)]
    rows = sum([_rows(f"shop{i}", 5) for i in range(10)], [])
    fatal, _ = scrape.check_health(results, [], rows)
    assert fatal and "povinných eshopov" in fatal[0]


def test_health_ignores_optional_shops_in_the_ratio():
    import scrape
    results = [_result(f"shop{i}", 5) for i in range(10)] + \
              [_result(f"opt{i}", 0, optional=True) for i in range(8)]
    rows = sum([_rows(f"shop{i}", 5) for i in range(10)], [])
    fatal, warnings = scrape.check_health(results, [], rows)
    assert not fatal
    assert sum("nepovinný" in w for w in warnings) == 8


def test_health_fails_on_big_total_drop():
    import scrape
    history = sum([_rows(f"shop{i}", 10, date="2026-08-29") for i in range(5)], [])
    results = [_result(f"shop{i}", 2) for i in range(5)]
    rows = sum([_rows(f"shop{i}", 2) for i in range(5)], [])
    fatal, _ = scrape.check_health(results, history, rows)
    assert fatal and "zaradených ponúk" in fatal[0]


def test_health_accepts_a_normal_day():
    import scrape
    history = sum([_rows(f"shop{i}", 10, date="2026-08-29") for i in range(5)], [])
    results = [_result(f"shop{i}", 10) for i in range(5)]
    rows = sum([_rows(f"shop{i}", 9) for i in range(5)], [])
    fatal, warnings = scrape.check_health(results, history, rows)
    assert not fatal and not warnings


# ------------------------------------------------------------- proxy

def test_via_proxy_is_off_without_env(monkeypatch):
    """Bez premenných sa nič nemení — proxy je doplnok, nie podmienka behu."""
    import scrape
    monkeypatch.setattr(scrape, "PROXY_URL", "")
    shop = {"id": "alza-cz", "proxy": True}
    assert scrape.via_proxy("https://www.alza.cz/x", shop) == "https://www.alza.cz/x"


def test_via_proxy_skips_unmarked_shops(monkeypatch):
    import scrape
    monkeypatch.setattr(scrape, "PROXY_URL", "https://proxy.workers.dev")
    monkeypatch.setattr(scrape, "PROXY_TOKEN", "tajne")
    shop = {"id": "pompo-cz"}
    assert scrape.via_proxy("https://pompo.cz/x", shop) == "https://pompo.cz/x"


def test_via_proxy_wraps_marked_shops(monkeypatch):
    import scrape
    monkeypatch.setattr(scrape, "PROXY_URL", "https://proxy.workers.dev/")
    monkeypatch.setattr(scrape, "PROXY_TOKEN", "taj ne&x")
    shop = {"id": "alza-cz", "proxy": True}
    built = scrape.via_proxy("https://www.alza.cz/a?b=1&c=2", shop)
    assert built.startswith("https://proxy.workers.dev?t=taj%20ne%26x&url=")
    assert "https%3A%2F%2Fwww.alza.cz%2Fa%3Fb%3D1%26c%3D2" in built


def test_proxied_shops_are_declared_in_config():
    """Každý eshop s `proxy: true` musí byť aj v ALLOWED_HOSTS Workera —
    zoznam sa generuje z tejto konfigurácie, tak nech sedí s realitou."""
    import yaml
    from pathlib import Path
    config = yaml.safe_load((Path(__file__).parent.parent / "config" / "shops.yaml")
                            .read_text(encoding="utf-8"))
    proxied = [s for s in config["shops"] if s.get("proxy")]
    assert proxied, "žiadny eshop nie je označený na proxy"
    for shop in proxied:
        assert shop.get("optional"), \
            f"{shop['id']}: eshop cez proxy musí zostať nepovinný, kým sa neoverí"


def test_history_rerun_replaces_same_day(tmp_path, monkeypatch):
    """Druhý beh v ten istý deň nesmie riadky zdvojiť — inak poistka porovná
    dnešok proti nafúknutému včerajšku a beh spadne na neexistujúci prepad."""
    import scrape
    monkeypatch.setattr(scrape, "DATA", tmp_path)

    scrape.append_history(_rows("shop1", 5, date="2026-08-28"))
    scrape.append_history(_rows("shop1", 4, date="2026-08-29"))
    scrape.append_history(_rows("shop1", 6, date="2026-08-29"))   # opakovaný beh

    history = scrape.read_history()
    assert sum(1 for r in history if r["date"] == "2026-08-28") == 5
    assert sum(1 for r in history if r["date"] == "2026-08-29") == 6


# ------------------------------------------------------------- portfólio

def _product(key, title, median, minimum):
    return {"key": key, "title": title, "median_eur": median, "min_eur": minimum,
            "min_any_eur": minimum or median}


def test_portfolio_computes_profit_and_loss():
    import scrape
    products = [_product("chaos-rising|booster-box", "Chaos Rising — Booster Box", 240.0, 219.0)]
    config = {"valuation": "median", "holdings": [
        {"key": "chaos-rising|booster-box", "qty": 2, "price": 200, "currency": "EUR"}]}
    fx = {"czk_eur": 1 / 24.0}
    portfolio = scrape.build_portfolio(products, config, fx)
    item = portfolio["items"][0]
    assert item["cost_eur"] == 400.0
    assert item["value_eur"] == 480.0
    assert item["pl_eur"] == 80.0
    assert item["pl_pct"] == 20.0
    assert portfolio["totals"]["pl_eur"] == 80.0


def test_portfolio_converts_purchase_in_czk():
    import scrape
    products = [_product("pitch-black|etb", "Pitch Black — ETB", 80.0, 78.0)]
    config = {"holdings": [
        {"key": "pitch-black|etb", "qty": 1, "price": 1899, "currency": "CZK"}]}
    portfolio = scrape.build_portfolio(products, config, {"czk_eur": 1 / 24.0})
    assert portfolio["items"][0]["cost_eur"] == round(1899 / 24.0, 2)


def test_portfolio_valuation_basis_min_is_more_conservative():
    import scrape
    products = [_product("k|f", "X", 240.0, 219.0)]
    holding = {"key": "k|f", "qty": 1, "price": 200, "currency": "EUR"}
    fx = {"czk_eur": 1 / 24.0}
    by_median = scrape.build_portfolio(products, {"valuation": "median", "holdings": [holding]}, fx)
    by_min = scrape.build_portfolio(products, {"valuation": "min", "holdings": [holding]}, fx)
    assert by_min["items"][0]["value_eur"] < by_median["items"][0]["value_eur"]


def test_portfolio_flags_unknown_key():
    """Preklep v key sa musí ohlásiť, nie ticho spadnúť pod stôl."""
    import scrape
    portfolio = scrape.build_portfolio(
        [], {"holdings": [{"key": "preklep|xxx", "qty": 1, "price": 50}]},
        {"czk_eur": 1 / 24.0})
    assert portfolio["items"][0]["found"] is False
    assert portfolio["totals"]["unmatched"] == 1


def test_empty_portfolio_is_not_an_error():
    import scrape
    portfolio = scrape.build_portfolio([], {}, {"czk_eur": 1 / 24.0})
    assert portfolio["items"] == []
    assert portfolio["totals"]["cost_eur"] == 0


# ------------------------------------------------------------- investičné metriky

def test_launch_price_table_covers_core_formats():
    for fmt in ("booster", "bundle", "booster-box", "etb"):
        assert classify.launch_price(fmt), f"{fmt} nemá uvádzaciu cenu"
    assert classify.launch_price("ultra-premium") is None, \
        "UPC nemá jednotnú uvádzaciu cenu, nesmie tam byť vymyslená"


def test_release_dates_are_present_and_parseable():
    from datetime import date
    dated = [e for e in classify.editions() if e.released]
    assert len(dated) >= 20, "väčšina edícií má mať dátum vydania"
    for edition in dated:
        date.fromisoformat(edition.released)


def test_older_sets_are_marked_out_of_print():
    """Sety staršie než ~18 mesiacov už zvyčajne nie sú v tlači — to je pri
    zapečatených produktoch hlavný dôvod, prečo cena rastie."""
    import scrape
    from datetime import date, timedelta
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=900)).isoformat()
    rows = [{"date": today, "shop_id": "s1", "edition_id": "obsidian-flames",
             "format_id": "etb", "variant": "", "packs": 9, "name": "x",
             "url": "https://x/", "price": 90, "currency": "EUR", "price_eur": 90,
             "per_pack_eur": 10, "in_stock": "1", "image": ""}]
    products, _ = scrape.build_products(rows, [], {}, today)
    assert products[0]["in_print"] is False
    assert products[0]["days_since_release"] > 550


def test_purchase_is_scored_against_cheapest_offer():
    """Kľúčová otázka portfólia: kúpil som pod cenou, za ktorú sa to dá kúpiť dnes?"""
    import scrape
    products = [_product("k|f", "X", 100.0, 90.0)]
    fx = {"czk_eur": 1 / 24.0}
    cheap = scrape.build_portfolio(products, {"holdings": [
        {"key": "k|f", "qty": 1, "price": 72, "currency": "EUR"}]}, fx)["items"][0]
    pricey = scrape.build_portfolio(products, {"holdings": [
        {"key": "k|f", "qty": 1, "price": 108, "currency": "EUR"}]}, fx)["items"][0]
    assert cheap["market_min_eur"] == 90.0
    assert cheap["vs_market_pct"] == -20.0     # kúpené o 20 % pod trhom
    assert pricey["vs_market_pct"] == 20.0
