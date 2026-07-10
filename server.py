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

app = Flask(__name__, static_folder=str(DOCS_DIR), static_url_path="")

_state: dict[str, Any] = {
    "running": False,
    "last