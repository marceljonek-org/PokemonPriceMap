"""Parsery katalógových stránok, jeden na platformu eshopu.

Adaptér nikdy nerozhoduje, či produkt patrí do monitoru — len vytiahne
všetko, čo na stránke je. Filtrovanie robí classify.py.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

NBSP = " "


@dataclass
class Offer:
    shop_id: str
    name: str
    url: str
    price: float | None
    currency: str
    in_stock: bool | None
    stock_text: str = ""
    image: str = ""
    sku: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

_PRICE_RE = re.compile(r"(\d[\d\s.,]*)")


def parse_price(text: str | None) -> float | None:
    """'1 399 Kč' -> 1399.0 ; '5,99 €' -> 5.99 ; '3399.00' -> 3399.0"""
    if not text:
        return None
    raw = html_lib.unescape(str(text)).replace(NBSP, " ").replace(" ", " ")
    m = _PRICE_RE.search(raw)
    if not m:
        return None
    num = m.group(1).strip().replace(" ", "")
    if "," in num and "." in num:
        num = num.replace(".", "").replace(",", ".") if num.rfind(",") > num.rfind(".") \
            else num.replace(",", "")
    elif "," in num:
        num = num.replace(",", ".")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None
    return value if value > 0 else None


IN_STOCK_WORDS = (
    "skladem", "skladom", "na sklade", "in stock", "instock", "dostupn",
    "ihned", "ihneď", "k odberu", "k odběru",
)
OUT_STOCK_WORDS = (
    "vyprodáno", "vyprodano", "vypredané", "vypredane", "vyprodán", "není skladem",
    "nie je skladom", "nedostupn", "out of stock", "outofstock", "sold out",
    "předobjednávka", "predobjednávka", "predobjednavka", "pre-order", "preorder",
    "na objednávku", "na objednavku", "očekáváme", "ocekavame", "momentálne nedostupné",
    "pripravujeme", "připravujeme", "nie je skladom", "není skladem",
)


def stock_from_text(text: str | None) -> bool | None:
    if not text:
        return None
    t = html_lib.unescape(text).replace(NBSP, " ").strip().lower()
    for w in OUT_STOCK_WORDS:      # negatívne slová majú prednosť
        if w in t:
            return False
    for w in IN_STOCK_WORDS:
        if w in t:
            return True
    return None


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text(strip=True)) if node else ""


def _attr(node, name: str) -> str:
    return (node.attributes.get(name) or "").strip() if node else ""


def link_rel_next(tree: HTMLParser, current_url: str) -> str | None:
    node = tree.css_first('link[rel="next"], a[rel="next"]')
    href = _attr(node, "href")
    return urljoin(current_url, href) if href else None


def max_page_links(tree: HTMLParser, current_url: str, selector: str) -> str | None:
    """Pre stránkovania bez rel=next: nájdi odkaz na nasledujúcu stranu."""
    current = 1
    m = re.search(r"[?&]page=(\d+)", current_url)
    if m:
        current = int(m.group(1))
    wanted = current + 1
    for a in tree.css(selector):
        href = _attr(a, "href")
        m = re.search(r"[?&]page=(\d+)", href)
        if m and int(m.group(1)) == wanted:
            return urljoin(current_url, href)
    return None


# ---------------------------------------------------------------- adapters

def parse_shoptet(tree: HTMLParser, shop: dict) -> list[Offer]:
    """Shoptet: mikrodáta data-micro-* sú spoľahlivejšie než vizuálne triedy."""
    offers = []
    for box in tree.css('[data-micro="product"]'):
        name = _text(box.css_first('[data-micro="name"]'))
        link = box.css_first('a[data-micro="url"]')
        url = urljoin(shop["base"], _attr(link, "href"))
        offer_node = box.css_first("[data-micro-price]")
        price = parse_price(_attr(offer_node, "data-micro-price"))
        if price is None:
            price = parse_price(_text(box.css_first(".price-final, .price")))
        avail = _attr(offer_node, "data-micro-availability").lower()
        in_stock = None
        if avail:
            in_stock = "instock" in avail.replace("-", "")
        stock_text = _text(box.css_first(".availability"))
        if in_stock is None:
            in_stock = stock_from_text(stock_text)
        image = _attr(box.css_first("[data-micro-image]"), "data-micro-image")
        if not image:
            image = _attr(box.css_first("img"), "data-src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=_attr(offer_node, "data-micro-price-currency") or shop["currency"],
            in_stock=in_stock, stock_text=stock_text, image=image,
            sku=_text(box.css_first('[data-micro="sku"]')),
        ))
    return offers


def parse_upgates(tree: HTMLParser, shop: dict) -> list[Offer]:
    offers = []
    for card in tree.css("article.card-item, .product-item[data-product-id]"):
        href = _attr(card, "data-href") or _attr(card.css_first("a"), "href")
        url = urljoin(shop["base"], href)
        name = _text(card.css_first("h4 a, .p-i-header a, h3 a"))
        if not name:
            name = _attr(card.css_first("img"), "alt")
        price = parse_price(_text(card.css_first(".price-main, .price-final, .p-i-price strong")))
        stock_text = _text(card.css_first(".availability"))
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=stock_from_text(stock_text),
            stock_text=stock_text,
            image=_attr(card.css_first("img"), "src"),
            sku=_attr(card, "data-product-id"),
        ))
    return offers


def parse_pompo(tree: HTMLParser, shop: dict) -> list[Offer]:
    """Pompo (wpj.cz): produkty sú v JSON-e data-tracking-view kategórie."""
    offers, seen = [], set()
    for node in tree.css("[data-tracking-view]"):
        raw = node.attributes.get("data-tracking-view") or ""
        try:
            data = json.loads(html_lib.unescape(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        impressions = data.get("impressions") or {}
        list_name = str(impressions.get("listName") or "")
        if not list_name.startswith("category"):
            continue
        for p in impressions.get("products") or []:
            pid = str(p.get("id"))
            if pid in seen:
                continue
            seen.add(pid)
            price = p.get("priceWithVat") or p.get("price")
            if not p.get("name") or not price:
                continue
            stock_text = str(p.get("availability") or "")
            offers.append(Offer(
                shop_id=shop["id"], name=p["name"],
                url=urljoin(shop["base"], str(p.get("url") or "")),
                price=float(price), currency=shop["currency"],
                in_stock=stock_from_text(stock_text), stock_text=stock_text,
                image=str(p.get("imageUrl") or ""), sku=pid,
            ))
    return offers


def parse_digihry(tree: HTMLParser, shop: dict) -> list[Offer]:
    offers = []
    for box in tree.css(".product-box[itemtype], [itemtype$='/Product']"):
        name = _text(box.css_first('[itemprop="name"]'))
        link = box.css_first('a[itemprop="url"]')
        url = urljoin(shop["base"], _attr(link, "href"))
        price_node = box.css_first('[itemprop="price"]')
        price = parse_price(_attr(price_node, "content") or _text(price_node))
        avail = _attr(box.css_first('[itemprop="availability"]'), "href").lower()
        in_stock = "instock" in avail.replace("-", "") if avail else None
        if in_stock is None:
            in_stock = stock_from_text(_text(box))
        image = _attr(box.css_first('meta[itemprop="image"]'), "content")
        if not image:
            image = _attr(box.css_first("img"), "data-src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=_attr(box.css_first('[itemprop="priceCurrency"]'), "content") or shop["currency"],
            in_stock=in_stock, stock_text=avail.rsplit("/", 1)[-1], image=image,
        ))
    return offers


def parse_pgs(tree: HTMLParser, shop: dict) -> list[Offer]:
    """PGS.sk / Smarty.cz: data-gaItem nesie názov aj dostupnosť, cena je v HTML."""
    offers = []
    for item in tree.css("div.productList-item"):
        ga = {}
        raw = item.attributes.get("data-gaitem") or item.attributes.get("data-gaItem") or ""
        if raw:
            try:
                ga = json.loads(html_lib.unescape(raw))
            except json.JSONDecodeError:
                ga = {}
        name = ga.get("name") or _text(item.css_first(".productList-item-title"))
        url = urljoin(shop["base"], _attr(item, "data-url")
                      or _attr(item.css_first("a.productList-item-title"), "href"))
        price = parse_price(_text(item.css_first(".productList-item-price")))
        stock_text = str(ga.get("available") or _text(item.css_first(".color-green, .color-red")))
        image = _attr(item.css_first("img.productList-item-img, img"), "src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=stock_from_text(stock_text),
            stock_text=stock_text, image=image, sku=_attr(item, "data-id"),
        ))
    return offers


def parse_veselydrak(tree: HTMLParser, shop: dict) -> list[Offer]:
    offers = []
    for item in tree.css("div.catalogue-item"):
        link = item.css_first("h3.product-name a") or item.css_first(".image-holder a")
        name = _text(item.css_first("h3.product-name")) or _attr(item.css_first("img"), "alt")
        url = urljoin(shop["base"], _attr(link, "href"))
        price = parse_price(_text(item.css_first("span.price")))
        stock_text = _text(item.css_first(".usual-price, .availability"))
        image = _attr(item.css_first("img"), "data-src") or _attr(item.css_first("img"), "src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=stock_from_text(stock_text),
            stock_text=stock_text, image=urljoin(shop["base"], image),
            sku=_attr(item, "data-lb-id"),
        ))
    return offers


def parse_alza(tree: HTMLParser, shop: dict) -> list[Offer]:
    offers = []
    for box in tree.css("div.box.browsingitem"):
        link = box.css_first("a.name")
        name = _text(link)
        url = urljoin(shop["base"], _attr(link, "href"))
        price = parse_price(_text(box.css_first(".ads-pb__price-value, .price_withVat, .c2")))
        stock_text = _text(box.css_first(".avlVal"))
        in_stock = stock_from_text(stock_text)
        if in_stock is None:
            in_stock = "canbuy" in (_attr(box, "class") or "").lower()
        image = _attr(box.css_first("img.box-image, img"), "src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=in_stock, stock_text=stock_text,
            image=image, sku=_attr(box, "data-code"),
        ))
    return offers


def parse_shopify(payload: dict, shop: dict) -> list[Offer]:
    """Shopify /products.json — 'available' je autoritatívne, text v šablóne nie."""
    offers = []
    for product in payload.get("products", []):
        variants = product.get("variants") or []
        if not variants:
            continue
        variant = variants[0]
        price = parse_price(variant.get("price"))
        if price is None:
            continue
        images = product.get("images") or []
        offers.append(Offer(
            shop_id=shop["id"], name=product.get("title", ""),
            url=f"{shop['base']}/products/{product.get('handle', '')}",
            price=price, currency=shop["currency"],
            in_stock=any(v.get("available") for v in variants),
            stock_text="available" if any(v.get("available") for v in variants) else "sold out",
            image=(images[0].get("src") if images else ""),
            sku=str(product.get("id") or ""),
        ))
    return offers


def parse_woocommerce(tree: HTMLParser, shop: dict) -> list[Offer]:
    """WooCommerce v dvoch podobách.

    Štandardná šablóna dáva `li.product` a stav skladu do tried položky.
    Stránky postavené v Oxygen Builderi (GeekHall) generujú vlastný
    `article.product_card` a stav píšu textom — preto dve vetvy.
    """
    items = tree.css("li.product")
    if not items:
        items = tree.css(".product_card")
        if items:
            return _parse_woocommerce_cards(items, shop)
    offers = []
    for item in items:
        classes = (_attr(item, "class") or "").lower()
        link = item.css_first("a.woocommerce-LoopProduct-link") or item.css_first("a")
        name = _text(item.css_first(".woocommerce-loop-product__title, h2, h3"))
        url = urljoin(shop["base"], _attr(link, "href"))
        # pri zľave je platná cena v <ins>, pôvodná v <del>
        price_node = item.css_first("ins .woocommerce-Price-amount") \
            or item.css_first(".woocommerce-Price-amount")
        price = parse_price(_text(price_node))
        in_stock = None
        if "outofstock" in classes:
            in_stock = False
        elif "instock" in classes:
            in_stock = True
        image = ""
        for node in item.css("img"):
            for attribute in ("data-src", "data-lazy-src", "src"):
                value = _attr(node, attribute)
                if value.startswith("http"):
                    image = value
                    break
            if image:
                break
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=in_stock,
            stock_text="outofstock" if in_stock is False else "instock",
            image=image, sku=_attr(item.css_first("[data-product_id]"), "data-product_id"),
        ))
    return offers


def _parse_woocommerce_cards(items, shop: dict) -> list[Offer]:
    """WooCommerce v Oxygen Builderi — stav skladu je text, nie CSS trieda."""
    offers = []
    for item in items:
        link = item.css_first("a[href]")
        name = _text(item.css_first("h2, h3, .ct-headline"))
        url = urljoin(shop["base"], _attr(link, "href"))
        price = parse_price(_text(item.css_first(".woocommerce-Price-amount")))
        stock_text = " ".join(_text(node) for node in item.css(".skladovost"))
        image = _attr(item.css_first("img.product-image, img"), "src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=stock_from_text(stock_text),
            stock_text=stock_text, image=image,
        ))
    return offers


def parse_xzone(tree: HTMLParser, shop: dict) -> list[Offer]:
    """Xzone.cz / Xzone.sk — vlastný systém, ceny v .price-box."""
    offers = []
    for item in tree.css("div.product-item"):
        link = item.css_first(".product-item-name a")
        name = _text(link) or _attr(link, "title")
        url = urljoin(shop["base"], _attr(link, "href"))
        price = None
        for node in item.css(".price-box span.price"):
            if node.tag == "span":            # <del class="price old-price"> je pôvodná cena
                price = parse_price(_text(node))
                break
        stock_text = _text(item.css_first(".expedice-date"))
        image = _attr(item.css_first(".product-image img"), "src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=stock_from_text(stock_text),
            stock_text=stock_text, image=image,
        ))
    return offers


def parse_opencart(tree: HTMLParser, shop: dict) -> list[Offer]:
    """OpenCart (dazzle.sk).

    Šablóna vykreslí každý produkt dvakrát — raz v zozname, raz v mriežke —
    preto čítame len mriežku, inak by bola každá ponuka duplicitná.
    Stužka „PREDOBJEDNÁVKA“ prebíja text o sklade: predobjednávka sa tvári
    ako „Skladom > 5 ks“, ale kúpiť sa to teraz nedá.
    """
    offers = []
    for item in tree.css(".product-grid .product"):
        link = item.css_first(".name a")
        name = _text(link)
        url = urljoin(shop["base"], _attr(link, "href"))
        price_node = item.css_first(".price-new") or item.css_first(".price")
        price = parse_price(_text(price_node))
        stock_text = _text(item.css_first(".stock"))
        in_stock = stock_from_text(stock_text)
        ribbons = _text(item.css_first(".ribbons")).lower()
        if "predobjedn" in ribbons or "preorder" in ribbons:
            in_stock = False
            stock_text = f"predobjednávka ({stock_text})" if stock_text else "predobjednávka"
        image_node = item.css_first("img")
        image = _attr(image_node, "data-echo") or _attr(image_node, "src")
        if image.endswith("blank.gif"):
            image = ""
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=in_stock, stock_text=stock_text,
            image=urljoin(shop["base"], image) if image else "",
        ))
    return offers


# ---------------------------------------------------------------- registry

def parse_sparkys(tree: HTMLParser, shop: dict) -> list[Offer]:
    """Sparkys/Alltoys: vlastná platforma s prefixom `rf-`.

    Celý názov je v atribúte `title` karty — text vnútri býva skrátený troma
    bodkami, takže by sa z neho edícia nedala spoľahlivo prečítať. Cena sa
    berie z `.rf-ProductCard-price`; vedľa nej býva prečiarknutá pôvodná cena
    v `.rf-ProductCard-originalPrice`, ktorú treba obísť, inak by sa zapisovala
    cena pred zľavou.
    """
    offers = []
    for card in tree.css(".rf-ProductCard"):
        name = _attr(card, "title") or _text(card.css_first(".rf-ProductCard-title"))
        link = card.css_first("a[itemprop='url'], a.rf-h-stretchedLink")
        url = urljoin(shop["base"], _attr(link, "href"))
        node = card.css_first(".rf-ProductCard-price .rf-Price-content")
        if node is None:
            # Bez zľavy nie je trieda --price, ale prvá cena na karte je tá platná.
            for candidate in card.css(".rf-Price-content"):
                parent = candidate.parent.parent if candidate.parent else None
                if parent and "originalPrice" in _attr(parent, "class"):
                    continue
                node = candidate
                break
        price = parse_price(_text(node))
        stock_text = _text(card.css_first(".rf-Stock-text"))
        image = _attr(card.css_first("img"), "data-src")
        if not name or price is None:
            continue
        offers.append(Offer(
            shop_id=shop["id"], name=name, url=url, price=price,
            currency=shop["currency"], in_stock=stock_from_text(stock_text),
            stock_text=stock_text, image=image,
        ))
    return offers


PARSERS = {
    "shoptet": parse_shoptet,
    "upgates": parse_upgates,
    "pompo": parse_pompo,
    "digihry": parse_digihry,
    "pgs": parse_pgs,
    "veselydrak": parse_veselydrak,
    "alza": parse_alza,
    "woocommerce": parse_woocommerce,
    "xzone": parse_xzone,
    "opencart": parse_opencart,
    "sparkys": parse_sparkys,
}


def parse(adapter: str, body: str, shop: dict) -> list[Offer]:
    if adapter == "shopify":
        return parse_shopify(json.loads(body), shop)
    tree = HTMLParser(body)
    return PARSERS[adapter](tree, shop)


def next_page(adapter: str, body: str, current_url: str) -> str | None:
    if adapter == "shopify":
        return None
    tree = HTMLParser(body)
    if adapter == "veselydrak":
        return max_page_links(tree, current_url, "a.pagination__page")
    if adapter == "woocommerce":
        node = tree.css_first("a.next.page-numbers, .woocommerce-pagination a.next")
        href = _attr(node, "href")
        return urljoin(current_url, href) if href else link_rel_next(tree, current_url)
    if adapter == "upgates":
        return link_rel_next(tree, current_url) or max_page_links(
            tree, current_url, ".pagination-wrap a, .pagination a")
    if adapter == "xzone":
        # Xzone nemá odkaz na ďalšiu stranu v HTML; ideme naslepo a scrape.py
        # zastaví, keď stránka nevráti ani jednu položku.
        current = 1
        match = re.search(r"[?&]page=(\d+)", current_url)
        if match:
            current = int(match.group(1))
        base = current_url.split("?")[0]
        return f"{base}?s=60&page={current + 1}"
    return link_rel_next(tree, current_url)
