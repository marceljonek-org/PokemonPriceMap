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
    patterns: tuple


@dataclass(frozen=True)
class Classification:
    edition: Edition
    format: Format
    packs: int | None
    variant: str = ""   # rozlíšenie samostatných kolekcií (napr. "mega-charizard-x-ex")


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
            patterns=tuple(re.compile(normalize(p), re.I) for p in f["patterns"]),
        )
        for f in raw["formats"]
    ]
    overrides = {
        (o["edition"], o["format"]): o["packs"] for o in raw.get("pack_overrides", [])
    }
    excludes = tuple(re.compile(normalize(p), re.I) for p in raw.get("exclude_patterns", []))
    launch = {k: float(v) for k, v in (raw.get("launch_price_eur") or {}).items()}
    return {
        "editions": editions,
        "formats": formats,
        "overrides": overrides,
        "excludes": excludes,
        "launch": launch,
    }


def launch_price(format_id: str) -> float | None:
    """Orientačná uvádzacia cena formátu v eurách, ak je známa."""
    return _config()["launch"].get(format_id)


def editions() -> list[Edition]:
    return _config()["editions"]


def formats() -> list[Format]:
    return _config()["formats"]


def edition_by_id(edition_id: str) -> Edition | None:
    return next((e for e in editions() if e.id == edition_id), None)


def format_by_id(format_id: str) -> Format | None:
    return next((f for f in formats() if f.id == format_id), None)


def is_excluded(name: str) -> bool:
    """Iné jazykové mutácie a produkty mimo štyroch sledovaných formátov."""
    n = normalize(name)
    return any(p.search(n) for p in _config()["excludes"])


def classify(name: str) -> Classification | None:
    """Vráti zaradenie alebo None, ak produkt do monitoru nepatrí."""
    if not name or is_excluded(name):
        return None
    n = normalize(name)
    if "pokemon" not in n and "pokémon" not in n:
        return None

    edition = next(
        (e for e in editions() if any(p.search(n) for p in e.patterns)), None
    )
    fmt = next((f for f in formats() if any(p.search(n) for p in f.patterns)), None)
    if fmt is None:
        return None
    if edition is None:
        # Premiové kolekcie sa často predávajú bez kódu setu (Mega Charizard X ex
        # UPC, Terapagos ex UPC). Sú to plnohodnotné zapečatené produkty, tak ich
        # nechávame pod zbernou edíciou namiesto zahodenia.
        if not fmt.edition_optional:
            return None
        edition = edition_by_id("standalone")
        if edition is None:
            return None
        variant = subject_of(n, fmt)
        packs = fmt.packs
        return Classification(edition=edition, format=fmt, packs=packs, variant=variant)

    packs = _config()["overrides"].get((edition.id, fmt.id), fmt.packs)
    return Classification(edition=edition, format=fmt, packs=packs)

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
