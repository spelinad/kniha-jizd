from __future__ import annotations

import base64
import json
import mimetypes
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .storage import (
    INPUTS_DIR,
    RECEIPTS_DIR,
    get_gemini_api_key,
    load_receipts_index,
    save_receipts_index,
)

ALLOWED_RECEIPT_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_EXCHANGE_RATES = {
    "CZK": 1.0,
    "EUR": 25.0,
    "PLN": 5.8,
    "HUF": 0.062,
}

GEMINI_PROMPT = """Jsi přesný účetní extraktor účtenek pro firemní knihu jízd v ČR.
Analyzuj přiloženou účtenku (PDF nebo obrázek) a vrať POUZE validní JSON objekt podle této struktury:

{
  "receipt_date": "YYYY-MM-DD",
  "merchant": "Název čerpací stanice nebo obchodu a pobočka",
  "currency": "CZK",
  "total_receipt_amount": 0.0,
  "items": [
    {
      "category": "PHM",
      "description": "Název paliva (např. DIESELbezBio nebo EVO Diesel)",
      "liters": 65.78,
      "unit_price": 37.50,
      "total_price": 2466.80
    }
  ]
}

Pravidla:
1. "receipt_date": najdi datum uskutečnění zdanitelného plnění / datum vytvoření dokladu (např. "Datum zdan. plnění", "Datum vytvoření", "DUZP") ve formátu YYYY-MM-DD. Pokud není čitelné, vrať null.
2. "currency": kód měny ("CZK", "EUR", "PLN", "HUF"). V ČR obvykle "CZK".
3. Rozpad položek "items":
   - Na jedné účtence může být současně palivo I další zboží/služby (např. AdBlue, kapalina do ostřikovačů, myčka). Každou položku uveď zvlášť!
   - "category":
     * "PHM" použij VÝHRADNĚ pro pohonné hmoty: nafta, diesel, DIESELbezBio, EVO Diesel, Verva, Natural 95, BA95, benzín, LPG, CNG, nabíjení kWh.
     * "OTHER" použij pro všechny ostatní výdaje k vozidlu: AdBlue (např. AD BLUE YARA), kapalina do ostřikovačů (např. CEPRO NANO), mytí auta / myčka, motorový olej, dálniční známka apod. POZOR: AdBlue patří VŽDY do "OTHER", nikdy do "PHM"!
   - Pro "PHM" vždy vyplň přesné množství "liters" (číslo), jednotkovou cenu za 1 litr vč. DPH "unit_price" (číslo) a celkovou cenu položky vč. DPH "total_price" (číslo). Pokud je na účtence jen celková cena a litry, dopočítej unit_price = round(total_price / liters, 2).
   - Pro "OTHER" nastav "liters": null, "unit_price": cena za kus nebo celkem, "total_price": celková cena položky vč. DPH. Do "description" napiš srozumitelný český popis do knihy jízd (např. "Kapalina do ostřikovačů (CEPRO NANO)", "AdBlue (YARA)", "Mytí vozu").
   - Položky nesouvisející s provozem auta (např. káva, bageta, občerstvení), pokud by na účtence byly, nezařazuj do PHM ani OTHER (ignoruj je, nebo pouze pokud tvoří čistě auto výdaje).
"""


def guess_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def list_input_receipt_files() -> list[dict[str, Any]]:
    if not INPUTS_DIR.exists():
        return []
    files: list[dict[str, Any]] = []
    for p in sorted(INPUTS_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in ALLOWED_RECEIPT_EXTS:
            files.append(
                {
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                    "mime_type": guess_mime_type(p.name),
                    "source": "inputs",
                }
            )
    return files


async def fetch_cnb_exchange_rate(currency: str, receipt_date: str | None = None) -> float:
    curr = (currency or "CZK").upper().strip()
    if curr == "CZK":
        return 1.0

    params: dict[str, str] = {}
    if receipt_date:
        try:
            dt = datetime.strptime(receipt_date, "%Y-%m-%d")
            params["date"] = dt.strftime("%d.%m.%Y")
        except ValueError:
            pass

    url = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    parts = line.split("|")
                    if len(parts) == 5 and parts[3].strip().upper() == curr:
                        amount = float(parts[2].replace(",", ".").strip())
                        rate = float(parts[4].replace(",", ".").strip())
                        return round(rate / amount, 4)
    except Exception:
        pass

    return DEFAULT_EXCHANGE_RATES.get(curr, 1.0)


DEFAULT_GEMINI_MODELS = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
]
_LAST_WORKING_MODEL: str | None = None


def infer_date_from_filename(filename: str) -> str | None:
    m = re.search(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", filename)
    if not m:
        return None
    candidate = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
        return candidate
    except ValueError:
        return None


def sync_receipts_index_with_inputs() -> dict[str, dict[str, Any]]:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    current_index = load_receipts_index()
    disk_files = {
        p.name: p
        for p in sorted(INPUTS_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in ALLOWED_RECEIPT_EXTS
    }

    updated: dict[str, dict[str, Any]] = {}
    for fname, path in disk_files.items():
        existing = current_index.get(fname)
        inferred = infer_date_from_filename(fname) or ""
        if existing:
            existing["filename"] = fname
            existing["size_bytes"] = path.stat().st_size
            existing["mime_type"] = guess_mime_type(fname)
            if not existing.get("receipt_date") and inferred:
                existing["receipt_date"] = inferred
            if not existing.get("assigned_month") and existing.get("receipt_date"):
                existing["assigned_month"] = existing["receipt_date"][:7]
            updated[fname] = existing
        else:
            updated[fname] = {
                "filename": fname,
                "size_bytes": path.stat().st_size,
                "mime_type": guess_mime_type(fname),
                "status": "unprocessed",
                "receipt_date": inferred,
                "assigned_month": inferred[:7] if inferred else "",
                "merchant": "",
                "currency": "CZK",
                "exchange_rate": 1.0,
                "fuel_items": [],
                "other_items": [],
                "error_message": "",
                "model_used": "",
                "updated_at": "",
            }

    save_receipts_index(updated)
    return updated


def build_month_entries_for_receipt(entry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fname = entry["filename"]
    rdate = entry.get("receipt_date") or ""
    merchant = entry.get("merchant") or ""
    curr = (entry.get("currency") or "CZK").upper().strip()
    rate = 1.0 if curr == "CZK" else float(entry.get("exchange_rate") or DEFAULT_EXCHANGE_RATES.get(curr, 1.0))

    fuel_entries: list[dict[str, Any]] = []
    for item in entry.get("fuel_items", []):
        liters = float(item.get("liters") or 0.0)
        unit_price = float(item.get("unit_price") or 0.0)
        if liters <= 0 and unit_price <= 0:
            continue
        fuel_entries.append(
            {
                "id": item.get("id") or uuid.uuid4().hex[:10],
                "source_filename": fname,
                "filename": fname,
                "date": rdate,
                "merchant": merchant,
                "description": item.get("description") or "Nafta",
                "liters": round(liters, 2),
                "currency": curr,
                "unit_price": round(unit_price, 2),
                "exchange_rate": rate,
                "total_czk": round(liters * unit_price * rate, 2),
            }
        )

    other_entries: list[dict[str, Any]] = []
    for item in entry.get("other_items", []):
        amount = float(item.get("amount") or 0.0)
        if amount <= 0:
            continue
        other_entries.append(
            {
                "id": item.get("id") or uuid.uuid4().hex[:10],
                "source_filename": fname,
                "filename": fname,
                "date": rdate,
                "merchant": merchant,
                "description": item.get("description") or "Ostatní výdaj",
                "amount": round(amount, 2),
                "currency": curr,
                "exchange_rate": rate,
                "project": item.get("project") or "",
            }
        )

    return fuel_entries, other_entries


def _extract_json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    return json.loads(cleaned)


def _rank_discovered_model(name: str) -> tuple[int, float, str]:
    low = name.lower()
    is_non_text = any(tok in low for tok in ("image", "tts", "audio", "robotics", "embedding", "imagen", "veo"))
    is_flash = "flash" in low
    is_lite = "lite" in low
    m_ver = re.search(r"gemini-(\d+(?:\.\d+)?)", low)
    ver = float(m_ver.group(1)) if m_ver else 0.0
    # Lower tuple sorts first
    tier = 0 if (is_flash and not is_lite and not is_non_text) else 1 if (is_flash and not is_non_text) else 2 if not is_non_text else 9
    return (tier, -ver, low)


async def _discover_gemini_models(client: httpx.AsyncClient, api_key: str) -> list[str]:
    try:
        resp = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key, "pageSize": 100},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return []
        models = resp.json().get("models") or []
        names: list[str] = []
        for m in models:
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" not in methods:
                continue
            raw_name = str(m.get("name") or "")
            short_name = raw_name.removeprefix("models/").strip()
            if short_name and "gemini" in short_name.lower():
                names.append(short_name)
        names.sort(key=_rank_discovered_model)
        return names
    except Exception:
        return []


def _parse_gemini_error(resp: httpx.Response) -> str:
    try:
        err_json = resp.json()
        return err_json.get("error", {}).get("message") or resp.text[:300]
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:300]}"


async def _generate_with_fallback(
    client: httpx.AsyncClient,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    global _LAST_WORKING_MODEL
    tried: set[str] = set()
    last_error = "Neznámá chyba při komunikaci s Gemini API."
    should_discover = False

    initial_candidates: list[str] = []
    if _LAST_WORKING_MODEL:
        initial_candidates.append(_LAST_WORKING_MODEL)
    initial_candidates.extend(DEFAULT_GEMINI_MODELS)

    async def _try_models(candidates: list[str]) -> tuple[str, dict[str, Any]] | None:
        nonlocal last_error, should_discover
        global _LAST_WORKING_MODEL
        for model in candidates:
            if model in tried:
                continue
            tried.add(model)
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                resp = await client.post(url, params={"key": api_key}, json=payload, timeout=25.0)
            except httpx.TimeoutException:
                last_error = f"Vypršel časový limit (25 s) u modelu {model}."
                should_discover = True
                continue
            except httpx.RequestError as exc:
                last_error = f"Chyba sítě u modelu {model}: {exc or type(exc).__name__}"
                continue

            if resp.status_code == 200:
                _LAST_WORKING_MODEL = model
                return model, resp.json()

            last_error = f"({resp.status_code}) u {model}: {_parse_gemini_error(resp)}"
            if resp.status_code in (404, 429, 500, 503):
                should_discover = True
                continue
            # Invalid key or permission errors fail immediately
            if resp.status_code in (400, 401, 403):
                raise RuntimeError(f"Gemini API vrátilo chybu {last_error}")
        return None

    res = await _try_models(initial_candidates)
    if res is not None:
        return res

    if should_discover:
        discovered = await _discover_gemini_models(client, api_key)
        res = await _try_models(discovered)
        if res is not None:
            return res

    raise RuntimeError(f"Gemini API vrátilo chybu {last_error}")


async def call_gemini_extract(file_bytes: bytes, mime_type: str, api_key: str) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Chybí Google Gemini API klíč. Nastavte jej v Nastavení nebo proměnné prostředí GEMINI_API_KEY.")

    b64_data = base64.b64encode(file_bytes).decode("ascii")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": GEMINI_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        model, body = await _generate_with_fallback(client, api_key, payload)

    candidates = body.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Model {model} nevrátil žádné kandidáty.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    parsed = _extract_json_from_text(text)
    parsed["_model"] = model
    return parsed


async def test_gemini_api_key(api_key: str) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "message": "Zadejte nejprve API klíč."}
    payload = {
        "contents": [{"parts": [{"text": "Odpověz jedním slovem: OK"}]}],
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            model, _ = await _generate_with_fallback(client, api_key, payload)
        return {"ok": True, "message": f"Spojení s Google Gemini API ({model}) je funkční."}
    except Exception as exc:
        return {"ok": False, "message": str(exc) or type(exc).__name__}


async def extract_receipt_to_entry(
    filename: str,
    content: bytes,
    fallback_month: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    key = (api_key or "").strip() or get_gemini_api_key()
    mime_type = guess_mime_type(filename)
    extracted = await call_gemini_extract(content, mime_type, key)

    inferred_date = infer_date_from_filename(filename) or ""
    receipt_date = extracted.get("receipt_date") or inferred_date
    currency = (extracted.get("currency") or "CZK").upper().strip()
    if currency not in ("CZK", "EUR", "PLN", "HUF", "HRK"):
        currency = "CZK"

    rate = await fetch_cnb_exchange_rate(currency, receipt_date)
    merchant = (extracted.get("merchant") or "").strip()
    raw_items = extracted.get("items") or []

    fuel_items: list[dict[str, Any]] = []
    other_items: list[dict[str, Any]] = []

    for item in raw_items:
        cat = str(item.get("category") or "").upper().strip()
        desc = str(item.get("description") or "").strip()
        total_price = float(item.get("total_price") or 0.0)
        if cat == "PHM":
            liters = float(item.get("liters") or 0.0)
            unit_price = float(item.get("unit_price") or 0.0)
            if unit_price <= 0 and liters > 0 and total_price > 0:
                unit_price = round(total_price / liters, 2)
            if liters <= 0 and unit_price > 0 and total_price > 0:
                liters = round(total_price / unit_price, 2)
            if liters > 0 or unit_price > 0:
                fuel_items.append(
                    {
                        "id": uuid.uuid4().hex[:10],
                        "description": desc or "Nafta",
                        "liters": round(liters, 2),
                        "unit_price": round(unit_price, 2),
                    }
                )
        elif cat == "OTHER" and total_price > 0:
            other_items.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "description": desc or "Ostatní výdaj",
                    "amount": round(total_price, 2),
                    "project": "",
                }
            )

    assigned_month = (
        receipt_date[:7]
        if receipt_date and re.match(r"^\d{4}-\d{2}", receipt_date)
        else fallback_month
    )

    return {
        "filename": filename,
        "size_bytes": len(content),
        "mime_type": mime_type,
        "status": "extracted_ai",
        "receipt_date": receipt_date,
        "assigned_month": assigned_month,
        "merchant": merchant,
        "currency": currency,
        "exchange_rate": rate,
        "fuel_items": fuel_items,
        "other_items": other_items,
        "error_message": "",
        "model_used": extracted.get("_model") or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_receipt_binary(filename: str, content: bytes) -> str:
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    receipt_id = uuid.uuid4().hex[:10]
    ext = Path(filename).suffix.lower() or ".pdf"
    stored_name = f"{receipt_id}{ext}"
    (RECEIPTS_DIR / stored_name).write_bytes(content)
    return stored_name


async def process_receipt_bytes(
    filename: str,
    content: bytes,
    target_month: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    key = (api_key or "").strip() or get_gemini_api_key()
    mime_type = guess_mime_type(filename)
    stored_name = save_receipt_binary(filename, content)

    extracted = await call_gemini_extract(content, mime_type, key)
    receipt_date = extracted.get("receipt_date")
    currency = (extracted.get("currency") or "CZK").upper().strip()
    if currency not in ("CZK", "EUR", "PLN", "HUF", "HRK"):
        currency = "CZK"

    rate = await fetch_cnb_exchange_rate(currency, receipt_date)
    merchant = (extracted.get("merchant") or "").strip()
    raw_items = extracted.get("items") or []

    fuel_entries: list[dict[str, Any]] = []
    other_entries: list[dict[str, Any]] = []
    receipt_id = Path(stored_name).stem

    for item in raw_items:
        cat = str(item.get("category") or "").upper().strip()
        desc = str(item.get("description") or "").strip()
        total_price = float(item.get("total_price") or 0.0)
        if cat == "PHM":
            liters = float(item.get("liters") or 0.0)
            unit_price = float(item.get("unit_price") or 0.0)
            if unit_price <= 0 and liters > 0 and total_price > 0:
                unit_price = round(total_price / liters, 2)
            if liters <= 0 and unit_price > 0 and total_price > 0:
                liters = round(total_price / unit_price, 2)
            fuel_entries.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "receipt_id": receipt_id,
                    "stored_file": stored_name,
                    "filename": filename,
                    "date": receipt_date or "",
                    "merchant": merchant,
                    "description": desc or "Nafta",
                    "liters": round(liters, 2),
                    "currency": currency,
                    "unit_price": round(unit_price, 2),
                    "exchange_rate": rate,
                    "total_czk": round(liters * unit_price * rate, 2),
                }
            )
        elif cat == "OTHER" and total_price > 0:
            other_entries.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "receipt_id": receipt_id,
                    "stored_file": stored_name,
                    "filename": filename,
                    "date": receipt_date or "",
                    "merchant": merchant,
                    "description": desc or "Ostatní výdaj",
                    "amount": round(total_price, 2),
                    "currency": currency,
                    "exchange_rate": rate,
                    "project": "",
                }
            )

    detected_month = receipt_date[:7] if receipt_date and len(receipt_date) >= 7 else None
    month_mismatch = bool(detected_month and detected_month != target_month)

    return {
        "receipt": {
            "id": receipt_id,
            "stored_file": stored_name,
            "filename": filename,
            "mime_type": mime_type,
            "receipt_date": receipt_date,
            "detected_month": detected_month,
            "month_mismatch": month_mismatch,
            "merchant": merchant,
            "currency": currency,
            "exchange_rate": rate,
            "total_receipt_amount": extracted.get("total_receipt_amount"),
            "model_used": extracted.get("_model"),
        },
        "fuel_entries": fuel_entries,
        "other_entries": other_entries,
    }
