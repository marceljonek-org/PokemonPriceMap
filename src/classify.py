"""Názov produktu -> edícia, formát, počet balíčkov.

Všetko je dátami riadené z config/editions.yaml, aby sa nová edícia dala pridať
bez zásahu do kódu. Čo sa nepodarí zaradiť, ide do data/unknown.csv.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "editions.yaml"
RIFTBOUND = Path(__file__).resolve().parent.parent / "config" / "riftbound.yaml"


def normalize(text: str) -> str:
    """Malé písmená, bez diakritiky, jednoduché medzery.

    Diakritiku zhadzujeme zámerne: eshopy píšu 'Pokémon' aj 'Pokemon',
    'výročie' aj 'vyrocie'.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace(" ", " ").replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+", " ", text).strip().lower()
    # Časť eshopov píše "Pokéball" jedným slovom. Bez zjednotenia by tá istá
    # plechovka bežala ako dva produkty s vlastnou cenou.
    return re.sub(r"\bpokeball\b", "poke ball", text)


@dataclass(frozen=True)
class Edition:
    id: str
    name: str
    code: str
    tier: str          # A/B/C podľa investičného rozboru, "" ak nie je zaradená
    series: str        # ME / SV / special
    released: str      # dátum vydania, "" ak nie je dohľadaný
    note: str
    patterns: tuple


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    short: str
    packs: int | None       # None = počet balíčkov sa líši podľa setu
    edition_optional: bool  # smie existovať aj bez rozpoznanej edície
    no_variant: bool        # meno pokémona na obale sa pri tomto formáte ignoruje
    patterns: tuple


@dataclass(frozen=True)
class Classification:
    edition: Edition
    format: Format
    packs: int | None
    variant: str = ""   # rozlíšenie samostatných kolekcií (napr. "mega-charizard-x-ex")
    game: str = "pokemon"   # "pokemon" alebo "riftbound"


@lru_cache(maxsize=1)
def _config() -> dict:
    with open(CONFIG, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    editions = [
        Edition(
            id=e["id"], name=e["name"], code=e.get("code") or "",
            tier=e.get("tier") or "", series=e.get("series") or "",
            released=str(e.get("released") or ""),
            note=e.get("note", ""),
            patterns=tuple(re.compile(normalize(p), re.I) for p in e["patterns"]),
        )
        for e in raw["editions"]
    ]
    formats = [
        Format(
            id=f["id"], name=f["name"], short=f["short"], packs=f.get("packs"),
            edition_optional=bool(f.get("edition_optional")),
            no_variant=bool(f.get("no_variant")),
            patterns=tuple(re.compile(normalize(p), re.I) for p in f["patterns"]),
        )
        for f in raw["formats"]
    ]
    overrides = {
        (o["edition"], o["format"]): o["packs"] for o in raw.get("pack_overrides", [])
    }
    excludes = tuple(re.compile(normalize(p), re.I) for p in raw.get("exclude_patterns", []))
    markers = tuple(re.compile(normalize(p), re.I) for p in raw.get("variant_markers", []))
    pokemons = tuple(
        re.compile(normalize(p), re.I) for p in raw.get("pokemon_variants", [])
    )
    launch = {k: float(v) for k, v in (raw.get("launch_price_eur") or {}).items()}
    return {
        "editions": editions,
        "formats": formats,
        "overrides": overrides,
        "markers": markers,
        "pokemons": pokemons,
        "excludes": excludes,
        "launch": launch,
    }


@lru_cache(maxsize=1)
def _riftbound() -> dict:
    """Konfigurácia druhej hry. Tvar je rovnaký ako `_config()`, aby sa dala
    použiť tým istým kódom — líši sa len obsahom a tým, že nemá úrovne A/B/C."""
    with open(RIFTBOUND, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    editions = [
        Edition(
            id=e["id"], name=e["name"], code=e.get("code") or "",
            tier=e.get("tier") or "", series=e.get("series") or "",
            released=str(e.get("released") or ""),
            note=e.get("note", ""),
            patterns=tuple(re.compile(normalize(p), re.I) for p in e["patterns"]),
        )
        for e in raw["editions"]
    ]
    formats = [
        Format(
            id=f["id"], name=f["name"], short=f["short"], packs=f.get("packs"),
            edition_optional=bool(f.get("edition_optional")),
            no_variant=bool(f.get("no_variant")),
            patterns=tuple(re.compile(normalize(p), re.I) for p in f["patterns"]),
        )
        for f in raw["formats"]
    ]
    return {
        "id": raw["game"]["id"],
        "name": raw["game"]["name"],
        "brand": tuple(re.compile(normalize(p), re.I)
                       for p in raw["game"]["brand_patterns"]),
        "editions": editions,
        "formats": formats,
        "excludes": tuple(re.compile(normalize(p), re.I)
                          for p in raw.get("exclude_patterns", [])),
        "champion_editions": dict(raw.get("champion_editions") or {}),
    }


GAMES = ("pokemon", "riftbound")


def launch_price(format_id: str) -> float | None:
    """Orientačná uvádzacia cena formátu v eurách, ak je známa."""
    return _config()["launch"].get(format_id)


def editions(game: str = "pokemon") -> list[Edition]:
    return (_riftbound() if game == "riftbound" else _config())["editions"]


def formats(game: str = "pokemon") -> list[Format]:
    return (_riftbound() if game == "riftbound" else _config())["formats"]


def edition_by_id(edition_id: str) -> Edition | None:
    """Edície oboch hier majú nezameniteľné id (Riftbound má predponu `rb-`),
    tak sa dá hľadať naprieč bez toho, aby volajúci vedel hru."""
    for game in GAMES:
        found = next((e for e in editions(game) if e.id == edition_id), None)
        if found:
            return found
    return None


def format_by_id(format_id: str, game: str = "pokemon") -> Format | None:
    return next((f for f in formats(game) if f.id == format_id), None)


def game_of(edition_id: str) -> str:
    return "riftbound" if edition_id.startswith("rb-") else "pokemon"


def is_excluded(name: str) -> bool:
    """Iné jazykové mutácie, príslušenstvo a produkty mimo sledovaných formátov.

    Vylúčenia oboch hier platia spoločne. Sú to príslušenstvá a cudzie jazyky —
    nič, čo by v druhej hre bolo legitímnym zapečateným produktom.
    """
    n = normalize(name)
    return any(p.search(n) for p in _config()["excludes"] + _riftbound()["excludes"])


def detect_game(name: str) -> str | None:
    """Ktorej hre názov patrí, alebo None, keď značku nespomína.

    Riftbound sa skúša prvý: jeho názvy hovoria o „League of Legends", nikdy
    o Pokémone, takže sa nemôžu pomýliť. Naopak pokémonie názvy slovo
    „riftbound" neobsahujú.
    """
    n = normalize(name)
    if any(p.search(n) for p in _riftbound()["brand"]):
        return "riftbound"
    if "pokemon" in n or "pokémon" in n:
        return "pokemon"
    return None


def classify(name: str, require_brand: bool = True) -> Classification | None:
    """Vráti zaradenie alebo None, ak produkt do monitoru nepatrí.

    Hru určuje názov: keď spomína Riftbound alebo League of Legends, ide do
    riftboundovej vetvy, inak do pokémonej. Edície, formáty aj vylúčenia sa
    potom hľadajú len v rámci tej jednej hry — vďaka tomu môže mať každá hra
    vlastný `booster-box` s vlastným počtom balíčkov.

    `require_brand` chráni eshopy, ktoré predávajú viac kartových hier: bez
    značky v názve by sa do monitoru dostal One Piece booster box. Na eshopoch,
    kde sú všetky sledované kategórie čisto pokémonie (`pokemon_only`
    v shops.yaml), sa naopak musí vypnúť — tam totiž značku v názvoch
    neopakujú a appka by zahodila skoro celý katalóg. Pri Riftbounde sa nevypína
    nikdy: jeho eshopy značku do názvov píšu.
    """
    if not name or is_excluded(name):
        return None
    n = normalize(name)
    game = detect_game(name)
    if game == "riftbound":
        return _classify_in("riftbound", n)
    if game is None and require_brand:
        return None
    return _classify_in("pokemon", n)


def _classify_in(game: str, n: str) -> Classification | None:
    """Zaradenie v rámci jednej hry. `n` je už normalizovaný názov."""
    edition = next(
        (e for e in editions(game) if any(p.search(n) for p in e.patterns)), None
    )
    fmt = next((f for f in formats(game) if any(p.search(n) for p in f.patterns)), None)
    if fmt is None:
        return None
    if edition is None:
        # Premiové kolekcie sa často predávajú bez kódu setu (Mega Charizard X ex
        # UPC, Terapagos ex UPC, riftboundový Arcane Box Set). Sú to plnohodnotné
        # zapečatené produkty, tak ich nechávame pod zbernou edíciou namiesto
        # zahodenia.
        if not fmt.edition_optional:
            return None
        if game == "riftbound":
            # Champion Deck bez názvu setu: šampión set jednoznačne určuje,
            # tak sa edícia doplní podľa neho namiesto zbernej.
            champion = champion_of(n)
            podla_mena = _riftbound()["champion_editions"].get(champion)
            if podla_mena:
                najdena = edition_by_id(podla_mena)
                if najdena:
                    return Classification(edition=najdena, format=fmt, packs=fmt.packs,
                                          variant=champion, game=game)
            edition = edition_by_id("rb-standalone")
            if edition is None:
                return None
            return Classification(edition=edition, format=fmt, packs=fmt.packs,
                                  variant=rb_subject_of(n), game=game)
        edition = edition_by_id("standalone")
        if edition is None:
            return None
        return Classification(edition=edition, format=fmt, packs=fmt.packs,
                              variant=subject_of(n, fmt), game=game)

    if game == "riftbound":
        # Riftbound nemá `pack_overrides` ani markery prevedenia. Šampión
        # v názve („Champion Deck - Vi") rozlišuje produkt, tak ide do variantu.
        return Classification(edition=edition, format=fmt, packs=fmt.packs,
                              variant=champion_of(n), game=game)

    packs = _config()["overrides"].get((edition.id, fmt.id), fmt.packs)
    return Classification(edition=edition, format=fmt, packs=packs,
                          variant=variant_of(n, fmt), game="pokemon")


# Šampión v názve decku. Eshopy ho píšu na obe strany názvu formátu:
# „Champion Deck - Vi", ale aj „Zed vs. Shen Showdown Deck". Keby sa hľadal len
# za ním, mala by Vendetta dva rôzne Showdowny — jeden s menom, jeden bez.
#
# Skúša sa najprv meno ZA formátom: v „Unleashed Champion Deck - Vi" je pred
# formátom názov setu, nie šampión, a ten by sa inak stal variantom.
_MENO = r"[a-z'’]+(?:\s*(?:vs\.?|&)\s*[a-z'’]+|\s+[a-z'’]+)?"
_CHAMPION_ZA = re.compile(r"(?:champion\s+deck|showdown)s?\s*(?:deck)?s?\s*[-:–]?\s*(" + _MENO + r")")
_CHAMPION_PRED = re.compile(r"\b(" + _MENO + r")\s+(?:champion\s+deck|showdown)")
# Slová, ktoré nie sú meno šampióna: názvy formátov, značka a mená edícií.
_NIE_MENO = {"deck", "decks", "display", "sealed", "edition", "en", "tcg",
             "riot", "games", "box", "pack", "bundle", "vault", "booster",
             "riftbound", "league", "of", "legends", "lol", "set", "one",
             "karetni", "kartova", "hra", "reprint", "starter"}


@lru_cache(maxsize=1)
def _nie_meno() -> frozenset:
    """K pevnému zoznamu pridá mená riftboundových edícií — „Unleashed Champion
    Deck - Vi" má pred formátom set, nie šampióna."""
    slova = set(_NIE_MENO)
    for e in editions("riftbound"):
        slova.update(w for w in re.split(r"[^a-z]+", normalize(e.name)) if w)
    return frozenset(slova)


def champion_of(n: str) -> str:
    """Meno šampióna z názvu riftboundového decku, alebo prázdny reťazec.

    Spojky sa zjednocujú na „vs", lebo Xzone píše „Evelynn & Seraphine"
    a Nekonečno „Evelynn vs. Seraphine" — je to ten istý produkt.
    """
    for vzor in (_CHAMPION_ZA, _CHAMPION_PRED):
        m = vzor.search(n)
        if not m:
            continue
        surove = re.sub(r"\s*(?:vs\.?|&)\s*", " vs ", m.group(1))
        slova = [w for w in re.split(r"[^a-z’']+", surove)
                 if w and w not in _nie_meno()]
        if slova and slova != ["vs"]:
            return "-".join(slova)
    return ""


# Zberné balenia (Arcane Box Set, Worlds Bundle 2025) nemajú kód setu. Názov
# vznikne z toho, čo v ňom ostane po odstránení značky a slov o formáte —
# `subject_of()` je stavané na pokémonie názvy a robilo z nich kašu.
_RB_SUM = re.compile(r"riftbound|league\s+of\s+legends|riot\s+games|\btcg\b|"
                     r"karetn[ií]|kartov[áa]|\bhra\b|sealed|\ben\b|\bbundle\b|"
                     r"box\s+set|\bset\b")


def rb_subject_of(n: str) -> str:
    zvysok = _RB_SUM.sub(" ", n)
    slova = [w for w in re.split(r"[^a-z0-9]+", zvysok) if w]
    return "-".join(slova[:4])


def variant_of(n: str, fmt: Format) -> str:
    """Rozlišovač prevedenia v rámci jednej edície a jedného formátu.

    Jedna edícia môže mať v tom istom formáte viac rôznych produktov — 30th
    Celebration má Ultra-Premium Collection v prevedení Day aj Night, ex Box
    v prevedení Sylveon aj Greninja. Bez rozlišovača by splynuli do jedného
    kľúča a medián by sa počítal z cien dvoch rôznych vecí.

    Dve výnimky, obe zistené na reálnych názvoch z eshopov:

    * Formát s `no_variant` (mini plechovky) meno pokémona ignoruje — deväť
      prevedení za rovnakú cenu je jeden produkt, nie deväť.
    * Keď v názve sedia DVE mená naraz ("Tin - Greninja ex, Sylveon ex"),
      nejde o tretí produkt, ale o spoločnú ponuku oboch. Meno sa zahodí a
      položka spadne ku generickému formátu, kde sa dá cena porovnať.
    """
    found = [m.group(0) for m in (p.search(n) for p in _config()["markers"]) if m]
    if not fmt.no_variant:
        pokemons = [m.group(0) for m in (p.search(n) for p in _config()["pokemons"]) if m]
        if len(pokemons) == 1:
            found += pokemons
    return "-".join(re.sub(r"[^a-z0-9]+", "-", w).strip("-") for w in found)

def looks_like_new_edition(name: str) -> bool:
    """Vyzerá to ako sledovaný formát, ale edíciu nepoznáme?

    Presne takto sa ohlási novo vydaný set — v ponuke sa objaví 'ME07 ...
    Booster Bundle', ktorý classify() zahodí. Zapíšeme ho do data/unknown.csv,
    nech je čo skontrolovať; bežné staré edície tam nechceme.
    """
    if not name or is_excluded(name):
        return False
    n = normalize(name)
    if "pokemon" not in n:
        return False
    if not any(p.search(n) for f in formats() for p in f.patterns):
        return False
    if any(p.search(n) for e in editions() for p in e.patterns):
        return False
    return bool(re.search(r"\bme\s*\d{1,2}(?:[.,]\d)?\b|\bsv\s*\d{1,2}(?:[.,]\d)?\b", n))


# Xzone.cz uvádza všetko ako "Karetní hra Pokémon TCG - ...", Xzone.sk ako
# "Kartová hra ...". Kým tu chýbalo české slovo, ten istý produkt z dvoch
# mutácií toho istého eshopu skončil pod dvoma kľúčmi.
_BASE_NOISE = (r"pokemon|pok[eé]mon|\btcg\b|\bkarty\b|\bkartov[aá]\b|\bhra\b|"
               r"\bkaretn[ií]\b|\bhry\b|"
               r"\bnov[ée]\b|\bnew\b|\bzberate[ľl]sk[áa]\b")
_NOISE = re.compile(_BASE_NOISE + r"|\(\d{4}\)|\b\d{4}\b")

# Množné čísla, ktoré eshopy striedajú pri tom istom produkte
# ("First Partners" vs "First Partner", "Poké Ball Tins" vs "Poké Ball Tin").
_PLURALS = {p: p[:-2] + "y" if p.endswith("ies") else p.rstrip("es") if p.endswith("xes") else p[:-1]
            for p in ("partners", "tins", "boxes", "packs", "decks", "cards",
                      "collections", "boosters", "blisters", "bundles", "trainers",
                      "toolkits", "figures", "tools", "sets")}
_NOISE_KEEP_YEARS = re.compile(_BASE_NOISE)


def subject_of(normalized_name: str, fmt: Format) -> str:
    """Z názvu samostatnej kolekcie vytiahne, čoho sa týka.

    'pokemon tcg: mega charizard x ex ultra premium collection (2025)'
    -> 'mega-charizard-x-ex'

    Bez toho by všetky Ultra Premium Collection splynuli do jedného produktu,
    lebo nemajú kód setu, podľa ktorého by sa dali rozlíšiť.
    """
    def stem(word: str) -> str:
        """Zjednotí jednotné a množné číslo, ale len pri slovách, ktoré pomenúvajú
        typ balenia. Plošné zhadzovanie koncového -s sa nedá použiť: pokazilo by
        mená pokémonov, ktoré sa na -s končia (Terapagos, Zapdos, Moltres)."""
        return _PLURALS.get(word, word)

    def slug(raw: str, drop_years: bool = True) -> str:
        cleaned = (_NOISE if drop_years else _NOISE_KEEP_YEARS).sub(" ", raw)
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
        return "-".join(stem(w) for w in cleaned.split()
                        if len(w) > 1 or w.isdigit())[:60]

    prefix, matched = normalized_name, ""
    for pattern in fmt.patterns:                 # odrež názov formátu
        match = pattern.search(normalized_name)
        if match:
            prefix = normalized_name[: match.start()]
            matched = match.group(0)
            break

    # Rozlišovač, ktorý stojí až ZA názvom formátu — "… Illustration Collection
    # - Series 3". Bez neho by Series 2 a Series 3 splynuli do jedného produktu
    # a medián by sa počítal z cien dvoch rôznych sérií.
    tail = ""
    if matched:
        rest = normalized_name[normalized_name.find(matched) + len(matched):]
        found = re.search(r"\b(?:series|serie|vol|volume)\.?\s*(\d{1,2})\b", rest)
        if found:
            tail = f"series-{found.group(1)}"

    subject = slug(prefix)
    if not subject and len(matched) <= 4:
        # Skratka môže stáť aj pred názvom ("SPC Charizard ex"). Vtedy je
        # predmetom to, čo nasleduje za ňou — inak by sa všetky takto písané
        # produkty zliali do jedného kľúča pomenovaného podľa skratky.
        # Len pri skratke: pri promo baleniach ("Pokémon Day 2026 Collection")
        # je identitou ročník a text za formátom je náhodný popis balenia.
        after = normalized_name[normalized_name.find(matched) + len(matched):]
        subject = slug(after)
    if subject:
        parts = subject.split("-")[:5]
        if tail:
            parts.append(tail)
        return "-".join(parts)

    # Pri promo baleniach nezostane pred názvom formátu nič — identitou je samotný
    # formát a ročník ("pokemon day 2026"). Rok berieme z celého názvu, nech sa
    # ten istý produkt z rôznych eshopov zaradí pod jeden kľúč.
    year = re.search(r"\b(20\d{2})\b", normalized_name)
    parts = [slug(matched)] + ([year.group(1)] if year else [])
    return "-".join(p for p in parts if p)
