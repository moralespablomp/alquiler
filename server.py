from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "searches.json"
RESULTS_JSON = DATA_DIR / "results.json"
RESULTS_CSV = DATA_DIR / "results.csv"

app = Flask(__name__, static_folder=str(DOCS_DIR), static_url_path="")

_state: dict[str, Any] = {
    "running": False,
    "last_success": None,
    "last_error": None,
    "log": [],
}
_state_lock = threading.Lock()


def append_log(message: str) -> None:
    with _state_lock:
        _state["log"].append(message.rstrip())
        _state["log"] = _state["log"][-300:]


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_config(payload: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "La configuración debe ser un objeto."

    filters = payload.get("filters")
    sources = payload.get("sources")
    if not isinstance(filters, dict) or not isinstance(sources, list):
        return None, "Faltan filtros o inmobiliarias."

    cleaned_sources: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            return None, f"La inmobiliaria {index} no es válida."
        name = str(source.get("name", "")).strip()
        urls = source.get("start_urls", [])
        if not name:
            return None, f"La inmobiliaria {index} no tiene nombre."
        if not isinstance(urls, list) or not urls:
            return None, f"{name} necesita al menos una URL."
        cleaned_urls = [str(url).strip() for url in urls if str(url).strip()]
        if not cleaned_urls or any(not valid_http_url(url) for url in cleaned_urls):
            return None, f"{name} contiene una URL inválida."
        cleaned_sources.append({
            "name": name,
            "enabled": bool(source.get("enabled", True)),
            "start_urls": cleaned_urls,
            "selectors": source.get("selectors", {}) if isinstance(source.get("selectors", {}), dict) else {},
        })

    cleaned = {"filters": filters, "sources": cleaned_sources}
    return cleaned, None


def run_scraper() -> None:
    with _state_lock:
        if _state["running"]:
            return
        _state["running"] = True
        _state["last_success"] = None
        _state["last_error"] = None
        _state["log"] = ["Iniciando búsqueda..."]

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.main"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if process.stdout is not None:
            for line in process.stdout:
                append_log(line)
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"El scraper terminó con código {return_code}.")
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


@app.get("/")
def index():
    return send_from_directory(DOCS_DIR, "index.html")


@app.get("/api/config")
def get_config():
    try:
        return jsonify(read_config())
    except (OSError, json.JSONDecodeError) as error:
        return jsonify({"error": f"No se pudo leer la configuración: {error}"}), 500


@app.put("/api/config")
def save_config():
    cleaned, error = validate_config(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return jsonify({"ok": True, "config": cleaned})
    except OSError as write_error:
        return jsonify({"error": f"No se pudo guardar: {write_error}"}), 500


@app.post("/api/run")
def start_search():
    with _state_lock:
        if _state["running"]:
            return jsonify({"ok": False, "error": "Ya hay una búsqueda en ejecución."}), 409
    try:
        config = read_config()
        enabled = [source for source in config.get("sources", []) if source.get("enabled")]
        if not enabled:
            return jsonify({"ok": False, "error": "Agregá y activá al menos una inmobiliaria."}), 400
    except (OSError, json.JSONDecodeError) as error:
        return jsonify({"ok": False, "error": f"Configuración inválida: {error}"}), 500

    threading.Thread(target=run_scraper, daemon=True).start()
    return jsonify({"ok": True, "message": "Búsqueda iniciada."})


@app.get("/api/status")
def search_status():
    with _state_lock:
        return jsonify(dict(_state))


@app.get("/api/results")
def results():
    if not RESULTS_JSON.exists():
        return jsonify({"generated_at": None, "total": 0, "filters": {}, "properties": []})
    try:
        return jsonify(json.loads(RESULTS_JSON.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        return jsonify({"error": f"No se pudieron leer los resultados: {error}"}), 500


@app.get("/api/results.csv")
def results_csv():
    if not RESULTS_CSV.exists():
        return jsonify({"error": "Todavía no existe un archivo CSV de resultados."}), 404
    return send_from_directory(DATA_DIR, "results.csv", as_attachment=True)


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(DOCS_DIR, path)


if __name__ == "__main__":
    print("Servidor disponible en http://127.0.0.1:8000")
    app.run(host="127.0.0.1", port=8000, debug=False)
