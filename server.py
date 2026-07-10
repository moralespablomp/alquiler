from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "searches.json"
RESULTS_JSON = DATA_DIR / "results.json"
RESULTS_CSV = DATA_DIR / "results.csv"
SELECTIONS_JSON = DATA_DIR / "selections.json"
GEOCODE_CACHE = DATA_DIR / "geocode_cache.json"
HOSPITAL = {
    "name": "Hospital Italiano de San Justo",
    "address": "Presidente Perón 2231, San Justo, Buenos Aires, Argentina",
    "lat": -34.6819,
    "lon": -58.5566,
}

app = Flask(__name__, static_folder=str(DOCS_DIR), static_url_path="")
_state: dict[str, Any] = {"running": False, "last_success": None, "last_error": None, "log": []}
_state_lock = threading.Lock()


def append_log(message: str) -> None:
    with _state_lock:
        _state["log"].append(message.rstrip())
        _state["log"] = _state["log"][-300:]


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_config() -> dict[str, Any]:
    return read_json(CONFIG_PATH, {})


def validate_config(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "La configuración no es válida."
    filters = payload.get("filters")
    portals = payload.get("portals")
    browser = payload.get("browser", {})
    if not isinstance(filters, dict) or not isinstance(portals, dict):
        return None, "Faltan filtros o portales."
    zones = [str(x).strip() for x in filters.get("zones", []) if str(x).strip()]
    if not zones:
        return None, "Agregá al menos una zona."
    allowed = {"zonaprop", "argenprop"}
    return {
        "filters": {**filters, "zones": zones},
        "portals": {key: bool(portals.get(key, False)) for key in allowed},
        "browser": {"headless": bool(browser.get("headless", False))},
    }, None


def run_scraper() -> None:
    with _state_lock:
        if _state["running"]:
            return
        _state.update({"running": True, "last_success": None, "last_error": None, "log": ["Iniciando búsqueda..."]})
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.main"], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        if process.stdout:
            for line in process.stdout:
                append_log(line)
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"El buscador terminó con código {code}.")
        with _state_lock:
            _state["last_success"] = True
        append_log("Búsqueda finalizada correctamente.")
    except Exception as error:
        with _state_lock:
            _state["last_success"] = False
            _state["last_error"] = str(error)
        append_log(f"ERROR: {error}")
    finally:
        with _state_lock:
            _state["running"] = False


def geocode(address: str) -> dict[str, Any] | None:
    normalized = " ".join(address.split()).strip()
    if not normalized:
        return None
    cache = read_json(GEOCODE_CACHE, {})
    if normalized in cache:
        return cache[normalized]
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": normalized, "format": "jsonv2", "limit": 1, "countrycodes": "ar"},
        headers={"User-Agent": "AlquilerPersonal/1.0 local-app"}, timeout=20,
    )
    response.raise_for_status()
    results = response.json()
    value = None
    if results:
        item = results[0]
        value = {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item.get("display_name", normalized)}
    cache[normalized] = value
    write_json(GEOCODE_CACHE, cache)
    time.sleep(1.05)
    return value


def route_to_hospital(lat: float, lon: float) -> dict[str, Any] | None:
    coords = f"{lon},{lat};{HOSPITAL['lon']},{HOSPITAL['lat']}"
    response = requests.get(
        f"https://router.project-osrm.org/route/v1/driving/{coords}",
        params={"overview": "false", "alternatives": "false", "steps": "false"}, timeout=20,
    )
    response.raise_for_status()
    routes = response.json().get("routes", [])
    if not routes:
        return None
    route = routes[0]
    return {"distance_km": round(route["distance"] / 1000, 1), "duration_min": round(route["duration"] / 60)}


@app.get("/")
def index():
    return send_from_directory(DOCS_DIR, "index.html")


@app.get("/api/config")
def get_config():
    return jsonify(read_config())


@app.put("/api/config")
def save_config():
    cleaned, error = validate_config(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400
    write_json(CONFIG_PATH, cleaned)
    return jsonify({"ok": True, "config": cleaned})


@app.post("/api/run")
def start_search():
    with _state_lock:
        if _state["running"]:
            return jsonify({"error": "Ya hay una búsqueda en ejecución."}), 409
    if not any(read_config().get("portals", {}).values()):
        return jsonify({"error": "Activá al menos un portal."}), 400
    threading.Thread(target=run_scraper, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/status")
def search_status():
    with _state_lock:
        return jsonify(dict(_state))


@app.get("/api/results")
def results():
    return jsonify(read_json(RESULTS_JSON, {"generated_at": None, "total": 0, "filters": {}, "properties": []}))


@app.get("/api/results.csv")
def results_csv():
    if not RESULTS_CSV.exists():
        return jsonify({"error": "Todavía no hay resultados."}), 404
    return send_from_directory(DATA_DIR, "results.csv", as_attachment=True)


@app.get("/api/selections")
def get_selections():
    return jsonify(read_json(SELECTIONS_JSON, {}))


@app.put("/api/selections/<property_id>")
def save_selection(property_id: str):
    payload = request.get_json(silent=True) or {}
    selections = read_json(SELECTIONS_JSON, {})
    selections[property_id] = {
        "selected": bool(payload.get("selected", True)),
        "status": str(payload.get("status", "Para revisar"))[:60],
        "notes": str(payload.get("notes", ""))[:4000],
        "updated_at": time.time(),
    }
    write_json(SELECTIONS_JSON, selections)
    return jsonify({"ok": True, "selection": selections[property_id]})


@app.delete("/api/selections/<property_id>")
def delete_selection(property_id: str):
    selections = read_json(SELECTIONS_JSON, {})
    selections.pop(property_id, None)
    write_json(SELECTIONS_JSON, selections)
    return jsonify({"ok": True})


@app.post("/api/map/compare")
def map_compare():
    payload = request.get_json(silent=True) or {}
    ids = set(payload.get("ids") or [])
    properties = read_json(RESULTS_JSON, {}).get("properties", [])
    compared = []
    for item in properties:
        if ids and item.get("id") not in ids:
            continue
        address = str(item.get("location") or "").strip()
        if not address:
            continue
        query = address if "argentina" in address.lower() else f"{address}, Buenos Aires, Argentina"
        try:
            point = geocode(query)
            if not point:
                compared.append({"id": item.get("id"), "error": "No se pudo ubicar", "address": address})
                continue
            route = route_to_hospital(point["lat"], point["lon"])
            compared.append({
                "id": item.get("id"), "title": item.get("title"), "url": item.get("url"),
                "price": item.get("price"), "currency": item.get("currency"), "address": address,
                "geocoded_address": point["display_name"], "lat": point["lat"], "lon": point["lon"],
                **(route or {}),
            })
        except Exception as error:
            compared.append({"id": item.get("id"), "error": str(error), "address": address})
    return jsonify({"hospital": HOSPITAL, "properties": compared})


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(DOCS_DIR, path)


if __name__ == "__main__":
    print("Servidor disponible en http://127.0.0.1:8000")
    app.run(host="127.0.0.1", port=8000, debug=False)
