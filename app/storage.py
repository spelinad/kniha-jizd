from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
INPUTS_DIR = Path(os.environ.get("INPUTS_DIR", PROJECT_ROOT / "Vstupy"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", PROJECT_ROOT / "Vystupy"))

MONTHS_DIR = DATA_DIR / "months"
RECEIPTS_DIR = DATA_DIR / "receipts"
CSV_DIR = DATA_DIR / "csv"
CONFIG_PATH = DATA_DIR / "config.json"
RECEIPTS_INDEX_PATH = DATA_DIR / "receipts_index.json"


def ensure_dirs() -> None:
    for d in (DATA_DIR, MONTHS_DIR, RECEIPTS_DIR, CSV_DIR, INPUTS_DIR, OUTPUTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG: dict[str, Any] = {
    "profile": {
        "vehicle_type": "Manažerský ",
        "license_plate": "7SK1179",
        "consumption_city": 5.0,
        "consumption_combined": 5.0,
        "initial_odometer": 7,
        "fuel_type": "nafta",
        "driver_name": "Dušan Špelina",
        "staff_number": "LPS00061645",
        "department": "620036",
        "year": 2026,
        "template_path": "Vstupy/Travel_book_2026_CZ_1.10.2026.xlsx",
        "output_path": "Vystupy/Travel_book_2026_CZ_7SK1179.xlsx",
        "gemini_api_key": "",
    },
    "business_routes": [
        {
            "id": "pre-praha",
            "name": "Jednání PRE (Praha)",
            "destination": "Praha 5 - Praha 4 - Praha 10",
            "km_city": 52,
            "km_other": 0,
            "reason": "jednání PRE",
            "project": " 300000000066041",
            "append_commute": True,
        },
        {
            "id": "cez-praha",
            "name": "Jednání ČEZ (Praha)",
            "destination": "Praha 5 - Praha 4 - Praha 5",
            "km_city": 46,
            "km_other": 0,
            "reason": "Jednání ČEZ",
            "project": "CZEMT2392",
            "append_commute": True,
        },
        {
            "id": "innogy-praha",
            "name": "Jednání innogy (Praha)",
            "destination": "Praha 5 - Praha 10 - Praha 5",
            "km_city": 52,
            "km_other": 0,
            "reason": "jednání innogy",
            "project": "103299",
            "append_commute": True,
        },
        {
            "id": "sse-zilina-tam",
            "name": "Jednání SSE (Praha → Žilina)",
            "destination": "Praha - Žilina SK",
            "km_city": 0,
            "km_other": 460,
            "reason": "jednání SSE",
            "project": "3*75554",
            "append_commute": False,
        },
        {
            "id": "sse-zilina-misto",
            "name": "Jednání SSE (pojížďky Žilina)",
            "destination": "Žilina",
            "km_city": 0,
            "km_other": 25,
            "reason": "jednání SSE",
            "project": "3*75554",
            "append_commute": False,
        },
        {
            "id": "sse-zilina-zpet",
            "name": "Jednání SSE (Žilina → Praha)",
            "destination": "Žilina SK - Praha",
            "km_city": 0,
            "km_other": 460,
            "reason": "jednání SSE",
            "project": "3*75554",
            "append_commute": False,
        },
        {
            "id": "brno-meeting",
            "name": "Team meeting (Brno otočka)",
            "destination": "Praha - Brno - Praha",
            "km_city": 0,
            "km_other": 440,
            "reason": "team meeting",
            "project": "Admin",
            "append_commute": False,
        },
    ],
    "private_routes": [
        {
            "id": "commute",
            "name": "Domov ↔ Práce (Středokluky - Praha)",
            "destination": "soukr. Středokluky - Praha",
            "min_km": 12,
            "max_km": 65,
            "typical_km": 35,
            "is_default_commute": True,
        },
        {
            "id": "praha",
            "name": "Pojížďky Praha a okolí",
            "destination": "soukr. Praha",
            "min_km": 45,
            "max_km": 105,
            "typical_km": 75,
            "is_default_commute": False,
        },
        {
            "id": "jizerky",
            "name": "Apartmán Jizerské hory",
            "destination": "soukr. Jizerské hory",
            "min_km": 115,
            "max_km": 265,
            "typical_km": 192,
            "is_default_commute": False,
        },
        {
            "id": "jiretin",
            "name": "Jiřetín pod Bukovou",
            "destination": "soukr. Jiřetín p. B.",
            "min_km": 125,
            "max_km": 310,
            "typical_km": 280,
            "is_default_commute": False,
        },
        {
            "id": "spindl",
            "name": "Špindlerův Mlýn",
            "destination": "soukr. Špindlerův mlýn",
            "min_km": 170,
            "max_km": 215,
            "typical_km": 190,
            "is_default_commute": False,
        },
        {
            "id": "plzen",
            "name": "Plzeň",
            "destination": "soukr. Plzeň",
            "min_km": 180,
            "max_km": 250,
            "typical_km": 220,
            "is_default_commute": False,
        },
        {
            "id": "klatovy",
            "name": "Klatovy",
            "destination": "soukr. Středokluky - Klatovy",
            "min_km": 125,
            "max_km": 165,
            "typical_km": 140,
            "is_default_commute": False,
        },
        {
            "id": "zadov",
            "name": "Zadov",
            "destination": "soukr. Zadov - Středokluky",
            "min_km": 160,
            "max_km": 200,
            "typical_km": 180,
            "is_default_commute": False,
        },
    ],
}


def _deep_merge_defaults(current: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(defaults)
    for k, v in current.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_defaults(v, merged[k])
        else:
            merged[k] = v
    return merged


def load_config() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return _deep_merge_defaults(data, DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config


def get_gemini_api_key(config: dict[str, Any] | None = None) -> str:
    if config is None:
        config = load_config()
    cfg_key = (config.get("profile") or {}).get("gemini_api_key", "").strip()
    if cfg_key:
        return cfg_key
    return os.environ.get("GEMINI_API_KEY", "").strip()


def resolve_project_path(raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def month_file_path(month_key: str) -> Path:
    ensure_dirs()
    safe_key = month_key.replace("/", "_").replace("\\", "_")
    return MONTHS_DIR / f"{safe_key}.json"


def load_month(month_key: str) -> dict[str, Any] | None:
    p = month_file_path(month_key)
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_month(month_key: str, data: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    p = month_file_path(month_key)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def list_saved_months() -> list[str]:
    ensure_dirs()
    return sorted(p.stem for p in MONTHS_DIR.glob("*.json"))


def load_receipts_index() -> dict[str, dict[str, Any]]:
    ensure_dirs()
    if not RECEIPTS_INDEX_PATH.exists():
        return {}
    with RECEIPTS_INDEX_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_receipts_index(index_data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ensure_dirs()
    with RECEIPTS_INDEX_PATH.open("w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)
    return index_data
