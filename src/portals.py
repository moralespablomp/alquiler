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
        return extract_jsonld(html, self.label, url) or extract_cards(html, self.label, url)


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


def _walk(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_walk(item))
    elif isinstance(value, dict):
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        accepted = {"Apartment", "House", "Residence", "Product", "Offer", "RealEstateListing", "ListItem"}
        if any(item in accepted for item in types):
            found.append(value)
        for child in value.values():
            if isinstance(child, (list, dict)):
                found.extend(_walk(child))
    return found


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
            address = nested.get("address") if isinstance(nested.get("address"), dict) else {}
            image = nested.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            url = nested.get("url") or offers.get("url") or item.get("url")
            title = nested.get("name") or item.get("name")
            if not url or not title:
                continue
            absolute = urljoin(page_url, str(url))
            if absolute in seen:
                continue
            seen.add(absolute)
            results.append({
                "source": source,
                "title": _text(title),
                "url": absolute,
                "price": _text(offers.get("price") or nested.get("price")),
                "currency": _text(offers.get("priceCurrency")),
                "location": _text(address),
                "description": _text(nested.get("description")),
                "image": _text(image),
            })
    return results


def extract_cards(html: str, source: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    selectors = [
        "[data-posting-type]",
        "[data-qa*='posting']",
        "article",
        ".listing__item",
        ".card",
    ]
    cards = []
    for selector in selectors:
        cards = soup.select(selector)
        if len(cards) >= 2:
            break
    for card in cards[:100]:
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
        image = card.select_one("img")
        price_match = re.search(r"(?:US\$|U\$S|USD|\$)\s*[\d.]+", text, re.I)
        results.append({
            "source": source,
            "title": _text(link.get("title")) or text[:140],
            "url": url,
            "price": price_match.group(0) if price_match else "",
            "currency": "USD" if price_match and "US" in price_match.group(0).upper() else "ARS",
            "location": text,
            "description": text,
            "image": urljoin(page_url, image.get("src", "")) if image else "",
        })
    return results


def open_context(playwright: Any, headless: bool) -> BrowserContext:
    browser = playwright.chromium.launch(headless=headless)
    return browser.new_context(
        locale="es-AR",
        viewport={"width": 1440, "height": 1000},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
    )
