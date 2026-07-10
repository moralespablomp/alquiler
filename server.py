from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
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


def run_scraper() -> None:
    with _state_lock:
        if _state["running"]:
            return
        _state["running"] = True
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
            _state["last_success"] = "Búsqueda finalizada correctamente."
        append_log("Búsqueda finalizada correctamente.")
    except Exception as error:
        with _state_lock:
            _state["last_error"] = str(error)
        append_log(f"ERROR: {error}")
    finally:
        with _state_lock:
            _state["running"] = False


@app.get("/")
def index():
    return send_from_directory(DOCS_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path: str):
    return send_from_directory(DOCS_DIR, path)


@app.post("/api/run")
def start_search():
    with _state_lock:
        if _state["running"]:
            return jsonify({"ok": False, "message": "Ya hay una búsqueda en ejecución."}), 409

    thread = threading.Thread(target=run_scraper, daemon=True)
    thread.start()
    return jsonify({"ok": True, "message": "Búsqueda iniciada."})


@app.get("/api/status")
def search_status():
    with _state_lock:
        return jsonify(dict(_state))


@app.get("/api/results")
def results():
    if not RESULTS_JSON.exists():
        return jsonify({
            "generated_at": None,
            "total": 0,
            "filters": {},
            "properties": [],
        })

    try:
        return jsonify(json.loads(RESULTS_JSON.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        return jsonify({"error": f"No se pudieron leer los resultados: {error}"}), 500


@app.get("/api/results.csv")
def results_csv():
    if not RESULTS_CSV.exists():
        return jsonify({"error": "Todavía no existe un archivo CSV de resultados."}), 404
    return send_from_directory(DATA_DIR, "results.csv", as_attachment=True)


if __name__ == "__main__":
    print("Servidor disponible en http://127.0.0.1:8000")
    app.run(host="127.0.0.1", port=8000, debug=False)
