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
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass(frozen=True)
class Edition:
    id: str
    name: str
    code: str
    tier: str
    note: str
    patterns: tuple


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    short: str
    packs: int
    patterns: tuple


@dataclass(frozen=True)
class Classification:
    edition: Edition
    format: Format
    packs: int


@lru_cache(maxsize=1)
def _config() -> dict:
    with open(CONFIG, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    editions = [
        Edition(
            id=e["id"], name=e["name"], code=e.get("code") or "", tier=e["tier"],
            note=e.get("note", ""),
            patterns=tuple(re.compile(normalize(p), re.I) for p in e["patterns"]),
        )
        for e in raw["editions"]
    ]
    formats = [
        Format(
            id=f["id"], name=f["name"], short=f["short"], packs=f["packs"],
            patterns=tuple(re.compile(normalize(p), re.I) for p in f["patterns"]),
        )
        for f in raw["formats"]
    ]
    overrides = {
        (o["edition"], o["format"]): o["packs"] for o in raw.get("pack_overrides", [])
    }
    excludes = tuple(re.compile(normalize(p), re.I) for p in raw.get("exclude_patterns", []))
    return {
        "editions": editions,
        "formats": formats,
        "overrides": overrides,
        "excludes": excludes,
    }


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
    if edition is None:
        return None

    fmt = next((f for f in formats() if any(p.search(n) for p in f.patterns)), None)
    if fmt is None:
        return None

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
