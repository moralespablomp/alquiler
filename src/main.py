from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from .portals import PORTALS, open_context

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "searches.json"
DATA_DIR = ROOT / "data"
RESULTS_JSON = DATA_DIR / "results.json"
RESULTS_CSV = DATA_DIR / "results.csv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
LOGGER = logging.getLogger(__name__)

POSITIVE = {"a estrenar": 25, "reciclado": 20, "refaccionado": 20, "remodelado": 15, "excelente estado": 15, "impecable": 12}
NEGATIVE = {"a refaccionar": -45, "estado original": -25, "de época": -20, "requiere mejoras": -35, "con humedad": -50, "para reciclar": -35}


@dataclass(slots=True)
class Property:
    id: str
    source: str
    title: str
    url: str
    price: int | None
    expenses: int | None
    currency: str
    location: str
    description: str
    property_type: str | None
    rooms: float | None
    area_m2: float | None
    parking: bool | None
    age_years: int | None
    condition_score: int
    condition_label: str
    image: str | None
    images: list[str]
    latitude: float | None
    longitude: float | None
    found_at: str


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_numeric_value(text: str) -> float | None:
    match = re.search(r"\d[\d.,]*", text.replace("\xa0", " "))
    if not match:
        return None
    raw = match.group(0).strip(".,")
    if not raw:
        return None

    # En avisos argentinos, un único separador seguido por tres dígitos suele ser miles.
    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            normalized = raw.replace(".", "").replace(",", ".")
        else:
            normalized = raw.replace(",", "")
    elif raw.count(".") > 1:
        normalized = raw.replace(".", "")
    elif raw.count(",") > 1:
        normalized = raw.replace(",", "")
    elif "." in raw:
        decimals = len(raw.split(".")[-1])
        normalized = raw.replace(".", "") if decimals == 3 else raw
    elif "," in raw:
        decimals = len(raw.split(",")[-1])
        normalized = raw.replace(",", "") if decimals == 3 else raw.replace(",", ".")
    else:
        normalized = raw

    try:
        return float(normalized)
    except ValueError:
        return None


def money(text: str, hinted: str = "") -> tuple[int | None, str]:
    combined = f"{text} {hinted}".lower()
    currency = "USD" if any(token in combined for token in ("usd", "us$", "u$s", "u$ s", "dólar", "dolar")) else "ARS"
    value = parse_numeric_value(text)
    return (round(value) if value is not None else None, currency)


def infer(pattern: str, text: str, cast=float):
    match = re.search(pattern, text, re.I)
    return cast(match.group(1).replace(",", ".")) if match else None


def condition(text: str) -> tuple[int, str]:
    score = 65
    lower = text.lower()
    score += sum(points for phrase, points in POSITIVE.items() if phrase in lower)
    score += sum(points for phrase, points in NEGATIVE.items() if phrase in lower)
    score = max(0, min(100, score))
    label = "Muy buen estado" if score >= 80 else "Estado aceptable" if score >= 60 else "Revisar estado" if score >= 45 else "Posible mal estado"
    return score, label


def normalized_images(raw: dict[str, Any]) -> list[str]:
    values = raw.get("images") if isinstance(raw.get("images"), list) else []
    primary = clean(raw.get("image"))
    result: list[str] = []
    for value in [primary, *values]:
        url = clean(value)
        if url and url.startswith("http") and url not in result:
            result.append(url)
    return result[:12]


def optional_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".")) if str(value or "").strip() else None
    except ValueError:
        return None


def normalize(raw: dict[str, Any]) -> Property:
    title = clean(raw.get("title")) or "Propiedad sin título"
    description = clean(raw.get("description"))
    location = clean(raw.get("location"))
    combined = f"{title} {description} {location}"
    price, currency = money(clean(raw.get("price")), clean(raw.get("currency")))
    expenses, _ = money(clean(raw.get("expenses")), "ARS")
    score, label = condition(combined)
    lower = combined.lower()
    property_type = next((p for p in ("departamento", "ph", "casa", "duplex", "monoambiente") if re.search(rf"\b{p}\b", lower)), None)
    parking = False if "sin cochera" in lower else True if any(x in lower for x in ("cochera", "garage", "garaje")) else None
    url = clean(raw.get("url"))
    images = normalized_images(raw)
    return Property(
        id=hashlib.sha256(f"{raw.get('source')}|{url}|{title}".encode()).hexdigest()[:16],
        source=clean(raw.get("source")), title=title, url=url, price=price, expenses=expenses, currency=currency,
        location=location, description=description, property_type=property_type,
        rooms=infer(r"(\d+(?:[.,]5)?)\s*(?:ambientes?|amb\b)", combined),
        area_m2=infer(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2|ts?2)", combined),
        parking=parking, age_years=infer(r"(\d+)\s*(?:años?|anos?)\s*(?:de\s*)?antig", combined, int),
        condition_score=score, condition_label=label, image=images[0] if images else None,
        images=images, latitude=optional_float(raw.get("latitude")), longitude=optional_float(raw.get("longitude")),
        found_at=datetime.now(timezone.utc).isoformat(),
    )


def matches(item: Property, filters: dict[str, Any]) -> bool:
    text = f"{item.title} {item.location} {item.description}".lower()
    zones = [z.lower() for z in filters.get("zones", [])]
    if zones and not any(z in text for z in zones): return False
    if item.rooms is not None and not (filters.get("min_rooms", 0) <= item.rooms <= filters.get("max_rooms", 99)): return False
    if item.price is not None and item.currency == "ARS" and item.price > filters.get("max_price", 10**15): return False
    if item.area_m2 is not None and item.area_m2 < filters.get("min_area_m2", 0): return False
    if filters.get("parking_required") and item.parking is not True: return False
    if item.age_years is not None and item.age_years > filters.get("max_age_years", 999): return False
    if item.condition_score < filters.get("exclude_condition_score_below", 0): return False
    if any(word.lower() in text for word in filters.get("excluded_words", [])): return False
    return True


def save(items: list[Property], filters: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    unique = {item.url or item.id: item for item in items}
    ordered = sorted(unique.values(), key=lambda x: (x.condition_score, -(x.price or 10**15)), reverse=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "total": len(ordered), "filters": filters, "properties": [asdict(x) for x in ordered]}
    RESULTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(Property.__dataclass_fields__.keys())
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        rows = []
        for item in ordered:
            row = asdict(item)
            row["images"] = " | ".join(row["images"])
            rows.append(row)
        writer.writerows(rows)


def main() -> None:
    config = load_config()
    filters = config.get("filters", {})
    enabled = [key for key, active in config.get("portals", {}).items() if active and key in PORTALS]
    if not enabled:
        raise RuntimeError("No hay portales habilitados.")
    raw_items: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        context = open_context(playwright, bool(config.get("browser", {}).get("headless", False)))
        page = context.new_page()
        for key in enabled:
            portal = PORTALS[key]
            for url in portal.search_urls(filters):
                LOGGER.info("Consultando %s: %s", portal.label, url)
                try:
                    found = portal.extract(page, url)
                    LOGGER.info("%s devolvió %s publicaciones", portal.label, len(found))
                    raw_items.extend(found)
                except Exception as error:
                    LOGGER.warning("No se pudo consultar %s: %s", portal.label, error)
        context.close()
    items = [normalize(raw) for raw in raw_items]
    filtered = [item for item in items if matches(item, filters)]
    save(filtered, filters)
    LOGGER.info("Informe generado con %s propiedades", len(filtered))


if __name__ == "__main__":
    main()
