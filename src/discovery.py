from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DETAIL_WORDS = re.compile(r"(propiedad|inmueble|departamento|casa|ph|duplex|ficha|detalle|listing|aviso)", re.I)
RENT_WORDS = re.compile(r"(alquiler|alquila|rent)", re.I)
SKIP_WORDS = re.compile(r"(venta|emprendimiento|tasacion|contacto|nosotros|servicios)", re.I)


def _same_host(url: str, root: str) -> bool:
    return urlparse(url).netloc == urlparse(root).netloc


def _score(url: str, zones: list[str]) -> int:
    normalized = url.lower()
    score = 0
    if RENT_WORDS.search(normalized):
        score += 5
    if DETAIL_WORDS.search(normalized):
        score += 4
    if re.search(r"\d{3,}", normalized):
        score += 2
    if any(zone.lower().replace(" ", "-") in normalized for zone in zones):
        score += 6
    if SKIP_WORDS.search(normalized):
        score -= 6
    return score


def _html_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for anchor in soup.select("a[href]"):
        url = urljoin(base_url, anchor.get("href", ""))
        if _same_host(url, base_url) and urlparse(url).scheme in {"http", "https"}:
            links.add(url.split("#", 1)[0])
    return list(links)


def _sitemap_urls(xml_text: str, base_url: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
        return [urljoin(base_url, node.text.strip()) for node in root.iter() if node.tag.endswith("loc") and node.text]
    except ET.ParseError:
        return re.findall(r"<loc[^>]*>(.*?)</loc>", xml_text, re.I | re.S)


def discover_listing_urls(start_url: str, headers: dict[str, str], timeout: int, zones: list[str] | None = None) -> list[str]:
    zones = zones or []
    session = requests.Session()
    session.headers.update(headers)
    candidates: set[str] = set()
    index_pages: set[str] = set()

    response = session.get(start_url, timeout=timeout)
    response.raise_for_status()
    root_url = response.url
    homepage_links = _html_links(response.text, root_url)
    for url in homepage_links:
        if RENT_WORDS.search(url):
            index_pages.add(url)
        if DETAIL_WORDS.search(url):
            candidates.add(url)

    sitemap_locations = {urljoin(root_url, "/sitemap.xml"), urljoin(root_url, "/sitemap_index.xml")}
    try:
        robots = session.get(urljoin(root_url, "/robots.txt"), timeout=timeout)
        if robots.ok:
            sitemap_locations.update(re.findall(r"^sitemap:\s*(\S+)", robots.text, re.I | re.M))
    except requests.RequestException:
        pass

    nested: set[str] = set()
    for sitemap in list(sitemap_locations)[:4]:
        try:
            sitemap_response = session.get(sitemap, timeout=timeout)
            if not sitemap_response.ok:
                continue
            for url in _sitemap_urls(sitemap_response.text, root_url):
                if "sitemap" in url.lower() and ".xml" in url.lower():
                    nested.add(url)
                elif DETAIL_WORDS.search(url) or RENT_WORDS.search(url):
                    candidates.add(url)
        except requests.RequestException:
            continue

    for sitemap in sorted(nested, key=lambda url: _score(url, zones), reverse=True)[:4]:
        try:
            sitemap_response = session.get(sitemap, timeout=timeout)
            if sitemap_response.ok:
                for url in _sitemap_urls(sitemap_response.text, root_url):
                    if DETAIL_WORDS.search(url) or RENT_WORDS.search(url):
                        candidates.add(url)
        except requests.RequestException:
            continue

    for index_url in sorted(index_pages, key=lambda url: _score(url, zones), reverse=True)[:4]:
        try:
            index_response = session.get(index_url, timeout=timeout)
            if index_response.ok:
                for url in _html_links(index_response.text, index_response.url):
                    if DETAIL_WORDS.search(url):
                        candidates.add(url)
        except requests.RequestException:
            continue

    ordered = sorted(candidates, key=lambda url: _score(url, zones), reverse=True)
    details = [url for url in ordered if DETAIL_WORDS.search(url) and not re.search(r"/(alquiler|propiedades|inmuebles)/?$", urlparse(url).path, re.I)]
    return details[:30] or [root_url]
