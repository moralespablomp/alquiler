from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext, Page

CAPTCHA_PHRASES = (
    "choose all", "select all", "verify you are human", "captcha", "curtains",
    "cloudflare", "access denied", "security check", "robot",
)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(clean_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(clean_text(item) for item in value.values())
    return re.sub(r"\s+", " ", str(value)).strip()


def valid_location(value: Any) -> str:
    text = clean_text(value)
    lower = text.lower()
    if not text or len(text) < 3 or any(phrase in lower for phrase in CAPTCHA_PHRASES):
        return ""
    if len(text) > 220:
        return ""
    return text


def location_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    known = {
        "ramos-mejia": "Ramos Mejía",
        "haedo": "Haedo",
        "castelar": "Castelar",
        "san-justo": "San Justo",
        "moron": "Morón",
        "villa-luzuriaga": "Villa Luzuriaga",
        "ciudadela": "Ciudadela",
        "lomas-del-mirador": "Lomas del Mirador",
    }
    return next((label for slug, label in known.items() if slug in path), "")


def address_from_dict(value: Any) -> str:
    if isinstance(value, str):
        return valid_location(value)
    if not isinstance(value, dict):
        return ""
    parts = [
        value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"),
        value.get("postalCode"), value.get("addressCountry"),
    ]
    return valid_location(", ".join(clean_text(part) for part in parts if clean_text(part)))


def image_urls(value: Any, base_url: str) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, list):
        for item in value:
            candidates.extend(image_urls(item, base_url))
    elif isinstance(value, dict):
        for key in ("url", "contentUrl", "thumbnailUrl"):
            if value.get(key):
                candidates.extend(image_urls(value[key], base_url))
    output: list[str] = []
    for candidate in candidates:
        absolute = urljoin(base_url, candidate.strip())
        if absolute.startswith("http") and absolute not in output:
            output.append(absolute)
    return output[:12]


def walk(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            found.extend(walk(item))
    elif isinstance(value, dict):
        found.append(value)
        for child in value.values():
            if isinstance(child, (list, dict)):
                found.extend(walk(child))
    return found


def detail_data(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    locations: list[str] = []
    images: list[str] = []
    description = ""

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in walk(payload):
            address = address_from_dict(item.get("address"))
            if address:
                locations.append(address)
            geo = item.get("geo")
            if isinstance(geo, dict) and geo.get("latitude") and geo.get("longitude"):
                pass
            images.extend(image_urls(item.get("image"), page_url))
            if not description:
                description = clean_text(item.get("description"))

    selectors = [
        '[data-qa*="location"]', '[data-qa*="address"]', '[class*="location"]',
        '[class*="address"]', 'h2', 'h3',
    ]
    for selector in selectors:
        for node in soup.select(selector)[:12]:
            candidate = valid_location(node.get_text(" ", strip=True))
            if candidate and any(token in candidate.lower() for token in ("ramos", "haedo", "castelar", "san justo", "morón", "moron", "calle", "av.", "avenida")):
                locations.append(candidate)

    for meta_selector in ('meta[property="og:description"]', 'meta[name="description"]'):
        node = soup.select_one(meta_selector)
        if node and not description:
            description = clean_text(node.get("content"))

    for image in soup.select("img"):
        candidate = image.get("src") or image.get("data-src") or image.get("data-lazy-src")
        if candidate:
            images.extend(image_urls(str(candidate), page_url))

    location = next((loc for loc in locations if re.search(r"\d", loc)), "")
    if not location:
        location = next(iter(locations), "")
    if not location:
        location = location_from_url(page_url)

    return {
        "location": valid_location(location),
        "description": description,
        "images": list(dict.fromkeys(images))[:12],
    }


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
        items = extract_jsonld(html, self.label, url) or extract_cards(html, self.label, url)
        detail_page = page.context.new_page()
        try:
            for item in items[:40]:
                try:
                    detail_page.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
                    detail_page.wait_for_timeout(1400)
                    detail = detail_data(detail_page.content(), item["url"])
                    if detail["location"]:
                        item["location"] = detail["location"]
                    elif not valid_location(item.get("location")):
                        item["location"] = location_from_url(item["url"])
                    if detail["description"]:
                        item["description"] = detail["description"]
                    merged = list(dict.fromkeys([*(item.get("images") or []), *detail["images"]]))[:12]
                    item["images"] = merged
                    item["image"] = merged[0] if merged else item.get("image", "")
                except Exception:
                    if not valid_location(item.get("location")):
                        item["location"] = location_from_url(item["url"])
        finally:
            detail_page.close()
        return items[:40]


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


def extract_jsonld(html: str, source: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in walk(payload):
            nested = item.get("item") if isinstance(item.get("item"), dict) else item
            offers = nested.get("offers") if isinstance(nested.get("offers"), dict) else {}
            url = nested.get("url") or offers.get("url") or item.get("url")
            title = nested.get("name") or item.get("name")
            absolute = urljoin(page_url, str(url or ""))
            if not url or not title or absolute in seen:
                continue
            if urlparse(absolute).netloc not in urlparse(page_url).netloc:
                continue
            seen.add(absolute)
            images = image_urls(nested.get("image") or item.get("image"), page_url)
            results.append({
                "source": source, "title": clean_text(title), "url": absolute,
                "price": clean_text(offers.get("price") or nested.get("price")),
                "currency": clean_text(offers.get("priceCurrency")),
                "location": address_from_dict(nested.get("address")) or location_from_url(absolute),
                "description": clean_text(nested.get("description")),
                "image": images[0] if images else "", "images": images,
            })
    return results


def extract_cards(html: str, source: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    cards = []
    for selector in ('[data-posting-type]', '[data-qa*="posting"]', 'article', '.listing__item', '.card'):
        cards = soup.select(selector)
        if len(cards) >= 2:
            break
    for card in cards[:100]:
        link = card.select_one("a[href]")
        if not link:
            continue
        url = urljoin(page_url, str(link.get("href", "")))
        if url in seen or urlparse(url).netloc not in urlparse(page_url).netloc:
            continue
        text = clean_text(card.get_text(" ", strip=True))
        if len(text) < 25 or any(phrase in text.lower() for phrase in CAPTCHA_PHRASES):
            continue
        seen.add(url)
        images: list[str] = []
        for image in card.select("img"):
            candidate = image.get("src") or image.get("data-src") or image.get("data-lazy-src")
            if candidate:
                images.extend(image_urls(str(candidate), page_url))
        price_match = re.search(r"(?:US\$|U\$S|USD|\$)\s*[\d.]+", text, re.I)
        results.append({
            "source": source,
            "title": clean_text(link.get("title")) or text[:140],
            "url": url,
            "price": price_match.group(0) if price_match else "",
            "currency": "USD" if price_match and "US" in price_match.group(0).upper() else "ARS",
            "location": location_from_url(url),
            "description": text,
            "image": images[0] if images else "",
            "images": list(dict.fromkeys(images))[:12],
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
