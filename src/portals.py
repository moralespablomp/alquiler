from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Page


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


@dataclass(frozen=True)
class PortalDefinition:
    key: str
    label: str
    base_url: str

    def search_urls(self, filters: dict[str, Any]) -> list[str]:
        zones = filters.get("zones") or []
        property_types = filters.get("property_types") or ["departamento"]
        return [self.build_url(zone, property_types, filters) for zone in zones]

    def build_url(self, zone: str, property_types: list[str], filters: dict[str, Any]) -> str:
        raise NotImplementedError

    def extract(self, page: Page, url: str) -> list[dict[str, Any]]:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)
        html = page.content()
        results = extract_jsonld(html, self.label, url) or extract_cards(html, self.label, url)
        return enrich_from_detail_pages(page, results, self.label)


class ZonapropPortal(PortalDefinition):
    def __init__(self) -> None:
        super().__init__("zonaprop", "Zonaprop", "https://www.zonaprop.com.ar")

    def build_url(self, zone: str, property_types: list[str], filters: dict[str, Any]) -> str:
        type_map = {"departamento": "departamentos", "ph": "ph", "casa": "casas"}
        types = "-".join(type_map.get(item, item) for item in property_types)
        rooms = filters.get("min_rooms")
        room_part = f"-{int(rooms)}-ambientes" if rooms else ""
        return f"{self.base_url}/{types}-alquiler-{slugify(zone)}{room_part}.html"


class ArgenpropPortal(PortalDefinition):
    def __init__(self) -> None:
        super().__init__("argenprop", "Argenprop", "https://www.argenprop.com")

    def build_url(self, zone: str, property_types: list[str], filters: dict[str, Any]) -> str:
        first_type = property_types[0] if property_types else "departamento"
        rooms = filters.get("min_rooms")
        room_part = f"-{int(rooms)}-ambientes" if rooms else ""
        return f"{self.base_url}/{slugify(first_type)}-alquiler-localidad-{slugify(zone)}{room_part}"


PORTALS: dict[str, PortalDefinition] = {
    "zonaprop": ZonapropPortal(),
    "argenprop": ArgenpropPortal(),
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    return re.sub(r"\s+", " ", str(value)).strip()


def _image_urls(value: Any, base_url: str) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, list):
        for item in value:
            candidates.extend(_image_urls(item, base_url))
    elif isinstance(value, dict):
        for key in ("url", "contentUrl", "thumbnailUrl"):
            if value.get(key):
                candidates.extend(_image_urls(value[key], base_url))
    result: list[str] = []
    for candidate in candidates:
        absolute = urljoin(base_url, candidate.strip())
        if absolute.startswith("http") and absolute not in result:
            result.append(absolute)
    return result[:12]


def _walk(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_walk(item))
    elif isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        accepted = {
            "Apartment", "House", "Residence", "Product", "Offer",
            "RealEstateListing", "ListItem", "Place", "Accommodation",
        }
        if any(item in accepted for item in types):
            found.append(value)
        for child in value.values():
            if isinstance(child, (list, dict)):
                found.extend(_walk(child))
    return found


def format_address(value: Any) -> str:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return ""
    ordered = [
        value.get("streetAddress"), value.get("addressLocality"),
        value.get("addressRegion"), value.get("postalCode"), value.get("addressCountry"),
    ]
    parts: list[str] = []
    for item in ordered:
        text = _text(item)
        if text and text not in parts:
            parts.append(text)
    return ", ".join(parts)


def extract_jsonld(html: str, source: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk(payload):
            nested = item.get("item") if isinstance(item.get("item"), dict) else item
            offers = nested.get("offers") if isinstance(nested.get("offers"), dict) else {}
            address = nested.get("address") or item.get("address")
            geo = nested.get("geo") if isinstance(nested.get("geo"), dict) else {}
            images = _image_urls(nested.get("image") or item.get("image"), page_url)
            url = nested.get("url") or offers.get("url") or item.get("url")
            title = nested.get("name") or item.get("name")
            if not url or not title:
                continue
            absolute = urljoin(page_url, str(url))
            if absolute in seen:
                continue
            seen.add(absolute)
            results.append({
                "source": source, "title": _text(title), "url": absolute,
                "price": _text(offers.get("price") or nested.get("price")),
                "currency": _text(offers.get("priceCurrency")),
                "expenses": "", "location": format_address(address),
                "latitude": _text(geo.get("latitude")), "longitude": _text(geo.get("longitude")),
                "description": _text(nested.get("description")),
                "image": images[0] if images else "", "images": images,
            })
    return results


def find_price_text(text: str) -> tuple[str, str]:
    patterns = [
        r"(?:U\s*\$\s*S|US\$|USD|U\$S)\s*[\d][\d.,]*",
        r"(?:ARS|AR\$)\s*[\d][\d.,]*",
        r"\$\s*[\d][\d.,]*",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = re.sub(r"\s+", "", match.group(0))
            currency = "USD" if re.search(r"USD|US\$|U\$S|U\s*\$\s*S", value, re.I) else "ARS"
            return value, currency
    return "", ""


def find_expenses_text(text: str) -> str:
    match = re.search(r"(?:expensas?|gastos comunes?)\s*(?:aprox\.?|estimadas?)?\s*[:$]?\s*([\d][\d.,]*)", text, re.I)
    return f"$ {match.group(1)}" if match else ""


def extract_cards(html: str, source: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    selectors = ["[data-posting-type]", "[data-qa*='posting']", "article", ".listing__item", ".card"]
    cards = []
    for selector in selectors:
        cards = soup.select(selector)
        if len(cards) >= 2:
            break
    for card in cards[:60]:
        link = card.select_one("a[href]")
        if not link:
            continue
        url = urljoin(page_url, str(link.get("href", "")))
        if url in seen:
            continue
        text = _text(card.get_text(" ", strip=True))
        if len(text) < 25:
            continue
        seen.add(url)
        images: list[str] = []
        for image in card.select("img"):
            candidate = image.get("src") or image.get("data-src") or image.get("data-lazy-src")
            if candidate:
                absolute_image = urljoin(page_url, str(candidate))
                if absolute_image not in images:
                    images.append(absolute_image)
        price, currency = find_price_text(text)
        results.append({
            "source": source, "title": _text(link.get("title")) or text[:140], "url": url,
            "price": price, "currency": currency or "ARS", "expenses": find_expenses_text(text),
            "location": "", "description": text,
            "image": images[0] if images else "", "images": images[:12],
        })
    return results


def detail_data_from_jsonld(html: str) -> tuple[str, str, str, list[str], str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    best_address = ""
    latitude = ""
    longitude = ""
    images: list[str] = []
    price = ""
    currency = ""
    expenses = ""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk(payload):
            nested = item.get("item") if isinstance(item.get("item"), dict) else item
            address = format_address(nested.get("address") or item.get("address"))
            if len(address) > len(best_address):
                best_address = address
            geo = nested.get("geo") if isinstance(nested.get("geo"), dict) else {}
            latitude = latitude or _text(geo.get("latitude"))
            longitude = longitude or _text(geo.get("longitude"))
            offers = nested.get("offers") if isinstance(nested.get("offers"), dict) else {}
            price = price or _text(offers.get("price") or nested.get("price"))
            currency = currency or _text(offers.get("priceCurrency") or nested.get("priceCurrency"))
            expenses = expenses or _text(nested.get("maintenanceFee") or nested.get("additionalProperty"))
            for image in _image_urls(nested.get("image") or item.get("image"), ""):
                if image not in images:
                    images.append(image)
    return best_address, latitude, longitude, images[:12], price, currency, expenses


def detail_location_from_dom(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        "[data-qa='POSTING_LOCATION']", "[data-qa*='LOCATION']", "[data-testid*='location']",
        "[class*='location']", "[class*='address']", "[id*='location']", "[id*='address']",
    ]
    candidates: list[str] = []
    for selector in selectors:
        for node in soup.select(selector)[:12]:
            text = _text(node.get_text(" ", strip=True))
            if 5 <= len(text) <= 180:
                candidates.append(text)
    for name in ("og:street-address", "place:location:address", "twitter:data1"):
        node = soup.select_one(f'meta[property="{name}"]') or soup.select_one(f'meta[name="{name}"]')
        if node and node.get("content"):
            candidates.append(_text(node.get("content")))
    address_pattern = re.compile(
        r"\b(?:calle|av\.?|avenida|ruta|presidente|pte\.?|general|gral\.?)?\s*"
        r"[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúÑñ.' -]{2,45}\s+\d{1,5}\b"
    )
    body_text = _text(soup.get_text(" ", strip=True))
    candidates.extend(match.group(0) for match in address_pattern.finditer(body_text))
    cleaned: list[str] = []
    for candidate in candidates:
        value = re.sub(r"\s+", " ", candidate).strip(" ,-|")
        if value and value not in cleaned:
            cleaned.append(value)
    with_number = [item for item in cleaned if re.search(r"\d", item)]
    return (with_number or cleaned or [""])[0]


def detail_price_from_dom(html: str) -> tuple[str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        "[data-qa*='PRICE']", "[data-testid*='price']", "[class*='price']",
        "[class*='Price']", "[id*='price']", "[id*='Price']",
    ]
    candidate_texts: list[str] = []
    for selector in selectors:
        for node in soup.select(selector)[:20]:
            text = _text(node.get_text(" ", strip=True))
            if 2 <= len(text) <= 180:
                candidate_texts.append(text)
    candidate_texts.append(_text(soup.get_text(" ", strip=True)))

    price = ""
    currency = ""
    expenses = ""
    for text in candidate_texts:
        if not price:
            price, currency = find_price_text(text)
        if not expenses:
            expenses = find_expenses_text(text)
        if price and expenses:
            break
    return price, currency, expenses


def enrich_from_detail_pages(page: Page, items: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, item in enumerate(items[:40], start=1):
        url = _text(item.get("url"))
        if not url:
            enriched.append(item)
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1400)
            html = page.content()
            address, latitude, longitude, detail_images, detail_price, detail_currency, detail_expenses = detail_data_from_jsonld(html)
            if not address:
                address = detail_location_from_dom(html)
            dom_price, dom_currency, dom_expenses = detail_price_from_dom(html)
            if address:
                item["location"] = address
            item["latitude"] = latitude or item.get("latitude", "")
            item["longitude"] = longitude or item.get("longitude", "")
            item["price"] = detail_price or dom_price or item.get("price", "")
            item["currency"] = detail_currency or dom_currency or item.get("currency", "")
            item["expenses"] = detail_expenses or dom_expenses or item.get("expenses", "")
            merged_images: list[str] = []
            for image in [*(item.get("images") or []), *detail_images]:
                absolute = urljoin(url, image)
                if absolute.startswith("http") and absolute not in merged_images:
                    merged_images.append(absolute)
            item["images"] = merged_images[:12]
            item["image"] = merged_images[0] if merged_images else item.get("image", "")
        except Exception:
            pass
        enriched.append(item)
    return enriched


def open_context(playwright: Any, headless: bool) -> BrowserContext:
    browser = playwright.chromium.launch(headless=headless)
    return browser.new_context(
        locale="es-AR", viewport={"width": 1440, "height": 1000},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
    )
