from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config" / "searches.json"
RESULTS_JSON = DATA_DIR / "results.json"
RESULTS_CSV = DATA_DIR / "results.csv"

app = Flask(__name__, static_folder=str(DOCS_DIR), static_url_path="")
_state: dict[str, Any] = {"running": False, "last_success": None, "last_error": None, "log": []}
_state_lock = threading.Lock()


def append_log(message: str) -> None:
    with _state_lock:
        _state["log"].append(message.rstrip())
        _state["log"] = _state["log"][-300:]


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


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
    cleaned_portals = {key: bool(portals.get(key, False)) for key in allowed}
    cleaned = {
        "filters": {**filters, "zones": zones},
        "portals": cleaned_portals,
        "browser": {"headless": bool(browser.get("headless", False))},
    }
    return cleaned, None


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
    CONFIG_PATH.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return jsonify({"ok": True, "config": cleaned})


@app.post("/api/run")
def start_search():
    with _state_lock:
        if _state["running"]:
            return jsonify({"error": "Ya hay una búsqueda en ejecución."}), 409
    config = read_config()
    if not any(config.get("portals", {}).values()):
        return jsonify({"error": "Activá al menos un portal."}), 400
    threading.Thread(target=run_scraper, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/status")
def search_status():
    with _state_lock:
        return jsonify(dict(_state))


@app.get("/api/results")
def results():
    if not RESULTS_JSON.exists():
        return jsonify({"generated_at": None, "total": 0, "filters": {}, "properties": []})
    return jsonify(json.loads(RESULTS_JSON.read_text(encoding="utf-8")))


@app.get("/api/results.csv")
def results_csv():
    if not RESULTS_CSV.exists():
        return jsonify({"error": "Todavía no hay resultados."}), 404
    return send_from_directory(DATA_DIR, "results.csv", as_attachment=True)


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(DOCS_DIR, path)


if __name__ == "__main__":
    print("Servidor disponible en http://127.0.0.1:8000")
    app.run(host="127.0.0.1", port=8000, debug=False)
