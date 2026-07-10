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
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "searches.json"
DATA_DIR = ROOT / "data"
RESULTS_JSON = DATA_DIR / "results.json"
RESULTS_CSV = DATA_DIR / "results.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AlquilerPersonal/1.0; "
        "+https://github.com/moralespablomp/alquiler)"
    )
}
TIMEOUT_SECONDS = 25

POSITIVE_WORDS = {
    "a estrenar": 25,
    "reciclado": 20,
    "refaccionado": 20,
    "remodelado": 15,
    "excelente estado": 15,
    "impecable": 12,
    "muy buen estado": 10,
}
NEGATIVE_WORDS = {
    "a refaccionar": -45,
    "estado original": -25,
    "de época": -20,
    "requiere mejoras": -35,
    "con humedad": -50,
    "para reciclar": -35,
    "deteriorado": -50,
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
LOGGER = logging.getLogger(__name__)


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
    found_at: str


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d[\d.]*)(?:[,](\d+))?", text.replace("\xa0", " "))
    if not match:
        return None
    integer = match.group(1).replace(".", "")
    decimal = match.group(2) or ""
    try:
        return float(f"{integer}.{decimal}" if decimal else integer)
    except ValueError:
        return None


def parse_money(text: str | None) -> tuple[int | None, str]:
    value = parse_number(text)
    normalized = (text or "").lower()
    currency = "USD" if "usd" in normalized or "u$s" in normalized else "ARS"
    return (round(value) if value is not None else None, currency)


def infer_rooms(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]5)?)\s*(?:ambientes?|amb\b)", text, re.I)
    return float(match.group(1).replace(",", ".")) if match else None


def infer_area(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2|ts?2|etros? cuadrados?)", text, re.I)
    return float(match.group(1).replace(",", ".")) if match else None


def infer_age(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:años?|anos?)\s*(?:de\s*)?antig", text, re.I)
    return int(match.group(1)) if match else None


def infer_parking(text: str) -> bool | None:
    normalized = text.lower()
    if any(term in normalized for term in ("sin cochera", "no posee cochera")):
        return False
    if any(term in normalized for term in ("cochera", "garage", "garaje")):
        return True
    return None


def infer_property_type(text: str) -> str | None:
    normalized = text.lower()
    for property_type in ("departamento", "ph", "casa", "duplex", "dúplex", "monoambiente"):
        if re.search(rf"\b{re.escape(property_type)}\b", normalized):
            return property_type.replace("dúplex", "duplex")
    return None


def evaluate_condition(text: str) -> tuple[int, str]:
    normalized = text.lower()
    score = 65
    for phrase, points in POSITIVE_WORDS.items():
        if phrase in normalized:
            score += points
    for phrase, points in NEGATIVE_WORDS.items():
        if phrase in normalized:
            score += points
    score = max(0, min(100, score))
    if score >= 80:
        label = "Muy buen estado"
    elif score >= 60:
        label = "Estado aceptable"
    elif score >= 45:
        label = "Revisar estado"
    else:
        label = "Posible mal estado"
    return score, label


def make_id(source: str, url: str, title: str) -> str:
    value = f"{source}|{url}|{title}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:16]


def node_text(node: Any, selector: str | None) -> str:
    if not selector:
        return ""
    selected = node.select_one(selector)
    return clean_text(selected.get_text(" ", strip=True)) if selected else ""


def node_attribute(node: Any, selector: str | None, attribute: str) -> str:
    if not selector:
        return ""
    selected = node.select_one(selector)
    return clean_text(selected.get(attribute)) if selected else ""


def property_from_raw(source_name: str, raw: dict[str, Any], base_url: str) -> Property:
    title = clean_text(raw.get("title")) or "Propiedad sin título"
    description = clean_text(raw.get("description"))
    features = clean_text(raw.get("features"))
    combined = " ".join((title, description, features))
    price, currency = parse_money(clean_text(raw.get("price")))
    expenses, _ = parse_money(clean_text(raw.get("expenses")))
    url = urljoin(base_url, clean_text(raw.get("url")))
    condition_score, condition_label = evaluate_condition(combined)

    return Property(
        id=make_id(source_name, url, title),
        source=source_name,
        title=title,
        url=url,
        price=price,
        expenses=expenses,
        currency=currency,
        location=clean_text(raw.get("location")),
        description=description,
        property_type=infer_property_type(combined),
        rooms=infer_rooms(combined),
        area_m2=infer_area(combined),
        parking=infer_parking(combined),
        age_years=infer_age(combined),
        condition_score=condition_score,
        condition_label=condition_label,
        image=urljoin(base_url, clean_text(raw.get("image"))) or None,
        found_at=datetime.now(timezone.utc).isoformat(),
    )


def extract_with_selectors(soup: BeautifulSoup, source: dict[str, Any], page_url: str) -> list[Property]:
    selectors = source.get("selectors", {})
    card_selector = selectors.get("card")
    if not card_selector:
        return []

    properties: list[Property] = []
    for card in soup.select(card_selector):
        raw = {
            "title": node_text(card, selectors.get("title")),
            "url": node_attribute(card, selectors.get("url"), "href"),
            "price": node_text(card, selectors.get("price")),
            "expenses": node_text(card, selectors.get("expenses")),
            "location": node_text(card, selectors.get("location")),
            "description": node_text(card, selectors.get("description")),
            "features": node_text(card, selectors.get("features")),
            "image": node_attribute(card, selectors.get("image"), "src"),
        }
        if raw["title"] or raw["url"]:
            properties.append(property_from_raw(source["name"], raw, page_url))
    return properties


def walk_jsonld(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            found.extend(walk_jsonld(item))
    elif isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        accepted = {"Apartment", "House", "SingleFamilyResidence", "Residence", "Product", "Offer"}
        if any(item in accepted for item in types):
            found.append(value)
        for key in ("@graph", "itemListElement", "item"):
            if key in value:
                found.extend(walk_jsonld(value[key]))
    return found


def extract_jsonld(soup: BeautifulSoup, source_name: str, page_url: str) -> list[Property]:
    properties: list[Property] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in walk_jsonld(payload):
            offers = item.get("offers", {}) if isinstance(item.get("offers"), dict) else {}
            address = item.get("address", {}) if isinstance(item.get("address"), dict) else {}
            image = item.get("image")
            if isinstance(image, list):
                image = image[0] if image else ""
            raw = {
                "title": item.get("name"),
                "url": item.get("url") or offers.get("url"),
                "price": offers.get("price") or item.get("price"),
                "location": address.get("addressLocality") or address.get("streetAddress"),
                "description": item.get("description"),
                "image": image,
            }
            if raw["title"] or raw["url"]:
                properties.append(property_from_raw(source_name, raw, page_url))
    return properties


def fetch_source(source: dict[str, Any]) -> list[Property]:
    results: list[Property] = []
    for url in source.get("start_urls", []):
        LOGGER.info("Consultando %s: %s", source["name"], url)
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as error:
            LOGGER.warning("No se pudo consultar %s: %s", url, error)
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        extracted = extract_with_selectors(soup, source, url)
        if not extracted:
            extracted = extract_jsonld(soup, source["name"], url)
        LOGGER.info("Se encontraron %s publicaciones", len(extracted))
        results.extend(extracted)
    return results


def matches_filters(item: Property, filters: dict[str, Any]) -> bool:
    searchable = f"{item.title} {item.location} {item.description}".lower()
    zones = [zone.lower() for zone in filters.get("zones", [])]
    if zones and not any(zone in searchable for zone in zones):
        return False

    allowed_types = [value.lower() for value in filters.get("property_types", [])]
    if allowed_types and item.property_type and item.property_type.lower() not in allowed_types:
        return False

    if item.rooms is not None:
        if item.rooms < filters.get("min_rooms", 0):
            return False
        if item.rooms > filters.get("max_rooms", float("inf")):
            return False

    if item.price is not None and item.currency == "ARS" and item.price > filters.get("max_price", float("inf")):
        return False
    if item.expenses is not None and item.expenses > filters.get("max_expenses", float("inf")):
        return False
    if item.area_m2 is not None and item.area_m2 < filters.get("min_area_m2", 0):
        return False
    if filters.get("parking_required") and item.parking is not True:
        return False
    if item.age_years is not None and item.age_years > filters.get("max_age_years", float("inf")):
        return False
    if item.condition_score < filters.get("exclude_condition_score_below", 0):
        return False

    excluded_words = [word.lower() for word in filters.get("excluded_words", [])]
    if any(word in searchable for word in excluded_words):
        return False
    return True


def deduplicate(properties: list[Property]) -> list[Property]:
    unique: dict[str, Property] = {}
    for item in properties:
        key = item.url or f"{item.title.lower()}|{item.price}|{item.location.lower()}"
        current = unique.get(key)
        if current is None or item.condition_score > current.condition_score:
            unique[key] = item
    return list(unique.values())


def save_results(properties: list[Property], filters: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        properties,
        key=lambda item: (item.condition_score, -(item.price or 10**15)),
        reverse=True,
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(ordered),
        "filters": filters,
        "properties": [asdict(item) for item in ordered],
    }
    RESULTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = list(asdict(ordered[0]).keys()) if ordered else [field.name for field in Property.__dataclass_fields__.values()]
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(item) for item in ordered)


def main() -> None:
    config = load_config()
    all_properties: list[Property] = []
    enabled_sources = [source for source in config.get("sources", []) if source.get("enabled")]

    if not enabled_sources:
        LOGGER.warning("No hay fuentes habilitadas. Editá config/searches.json para comenzar.")

    for source in enabled_sources:
        all_properties.extend(fetch_source(source))

    filtered = [item for item in all_properties if matches_filters(item, config.get("filters", {}))]
    filtered = deduplicate(filtered)
    save_results(filtered, config.get("filters", {}))
    LOGGER.info("Informe generado con %s propiedades", len(filtered))


if __name__ == "__main__":
    main()
