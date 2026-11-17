from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .csv_service import (
    aggregate_trips_by_day,
    get_available_csv_months,
    load_all_csv_trips,
    reconcile_month_days,
)
from .excel_service import compute_month_summary, export_travel_book_excel
from .receipt_service import (
    ALLOWED_RECEIPT_EXTS,
    build_month_entries_for_receipt,
    extract_receipt_to_entry,
    fetch_cnb_exchange_rate,
    guess_mime_type,
    infer_date_from_filename,
    list_input_receipt_files,
    process_receipt_bytes,
    sync_receipts_index_with_inputs,
    test_gemini_api_key,
)
from .routing_service import calculate_route_distance
from .storage import (
    CSV_DIR,
    INPUTS_DIR,
    RECEIPTS_DIR,
    ensure_dirs,
    get_gemini_api_key,
    list_saved_months,
    load_config,
    load_month,
    load_receipts_index,
    save_config,
    save_month,
    save_receipts_index,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Kniha jízd 2026", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _parse_month_key(month_key: str) -> tuple[int, int]:
    try:
        parts = month_key.split("-")
        year = int(parts[0])
        month = int(parts[1])
        if not (1 <= month <= 12):
            raise ValueError
        return year, month
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Neplatný formát měsíce: {month_key}") from exc


def _get_or_build_month(month_key: str, force_reestimate: bool = False, reset_all: bool = False) -> dict[str, Any]:
    year, month = _parse_month_key(month_key)
    config = load_config()
    all_trips, _ = load_all_csv_trips()
    daily_csv = aggregate_trips_by_day(all_trips)

    existing = load_month(month_key) or {
        "month": month_key,
        "days": [],
        "fuel_receipts": [],
        "other_expenses": [],
        "uploaded_receipts": [],
        "pending_receipts": [],
        "exchange_rates": {"EUR": 25.0, "PLN": 5.8, "HUF": 0.062},
    }

    existing_days = existing.get("days", [])
    if reset_all:
        # Keep only business trips if reset_all, or clear locks on private days
        cleaned_days = []
        for d in existing_days:
            if int(d.get("km_city", 0) or 0) > 0 or int(d.get("km_other", 0) or 0) > 0:
                cleaned_days.append(d)
        existing_days = cleaned_days

    days = reconcile_month_days(
        year=year,
        month=month,
        daily_csv=daily_csv,
        existing_days=existing_days,
        config=config,
        force_reestimate=force_reestimate,
    )
    existing["month"] = month_key
    existing["days"] = days
    existing.setdefault("fuel_receipts", [])
    existing.setdefault("other_expenses", [])
    existing.setdefault("uploaded_receipts", [])
    existing.setdefault("pending_receipts", [])
    existing.setdefault("exchange_rates", {"EUR": 25.0, "PLN": 5.8, "HUF": 0.062})
    save_month(month_key, existing)
    return existing


@app.on_event("startup")
def _startup() -> None:
    ensure_dirs()
    load_config()


@app.get("/", response_class=HTMLResponse)
def index_page() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


def _sorted_receipt_files(index_data: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        index_data.values(),
        key=lambda x: (x.get("receipt_date") or "9999-99-99", x.get("filename") or ""),
    )


def _sync_receipt_entry_to_months(
    filename: str,
    entry: dict[str, Any],
    prev_month: str | None = None,
) -> None:
    target_month = (entry.get("assigned_month") or "").strip()
    months_to_clean = {m for m in (prev_month, target_month) if m}
    for mk in months_to_clean:
        _parse_month_key(mk)
        mdata = _get_or_build_month(mk)
        mdata["fuel_receipts"] = [
            item
            for item in mdata.get("fuel_receipts", [])
            if item.get("source_filename") != filename and item.get("filename") != filename
        ]
        mdata["other_expenses"] = [
            item
            for item in mdata.get("other_expenses", [])
            if item.get("source_filename") != filename and item.get("filename") != filename
        ]
        if mk == target_month and entry.get("status") in ("extracted_ai", "manual"):
            fuel_entries, other_entries = build_month_entries_for_receipt(entry)
            mdata["fuel_receipts"].extend(fuel_entries)
            mdata["other_expenses"].extend(other_entries)
            curr = (entry.get("currency") or "CZK").upper().strip()
            if curr != "CZK" and entry.get("exchange_rate"):
                mdata.setdefault("exchange_rates", {})[curr] = float(entry["exchange_rate"])
        save_month(mk, mdata)


@app.get("/api/init")
def api_init() -> dict[str, Any]:
    config = load_config()
    all_trips, csv_files = load_all_csv_trips()
    daily_csv = aggregate_trips_by_day(all_trips)
    csv_months = get_available_csv_months(daily_csv)
    saved_months = list_saved_months()
    input_receipts = list_input_receipt_files()
    receipt_index = sync_receipts_index_with_inputs()

    default_month = "2026-09"
    if default_month not in csv_months:
        if csv_months:
            default_month = sorted(csv_months.keys())[-1]
        else:
            default_month = f"{config['profile'].get('year', 2026)}-09"

    has_env_key = bool(get_gemini_api_key(config))
    return {
        "config": config,
        "gemini_key_configured": has_env_key,
        "csv_files": csv_files,
        "csv_months": csv_months,
        "saved_months": saved_months,
        "input_receipt_files": input_receipts,
        "receipt_files": _sorted_receipt_files(receipt_index),
        "default_month": default_month,
    }


@app.get("/api/month/{month_key}")
def api_get_month(month_key: str) -> dict[str, Any]:
    config = load_config()
    month_data = _get_or_build_month(month_key)
    summary = compute_month_summary(month_data, config)
    all_trips, csv_files = load_all_csv_trips()
    csv_months = get_available_csv_months(aggregate_trips_by_day(all_trips))
    return {
        "month_data": month_data,
        "summary": summary,
        "csv_files": csv_files,
        "csv_months": csv_months,
    }


@app.put("/api/month/{month_key}")
def api_save_month(month_key: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _parse_month_key(month_key)
    config = load_config()
    payload["month"] = month_key
    save_month(month_key, payload)
    summary = compute_month_summary(payload, config)
    return {"month_data": payload, "summary": summary}


@app.post("/api/month/{month_key}/reestimate")
def api_reestimate_month(month_key: str, reset_all: bool = False) -> dict[str, Any]:
    config = load_config()
    month_data = _get_or_build_month(month_key, force_reestimate=True, reset_all=reset_all)
    summary = compute_month_summary(month_data, config)
    return {"month_data": month_data, "summary": summary}


class BusinessTripInput(BaseModel):
    date: str
    destination: str
    km_city: int = 0
    km_other: int = 0
    reason: str = ""
    project: str = ""
    append_commute: bool = False


@app.post("/api/month/{month_key}/business-trip")
def api_add_business_trip(month_key: str, body: BusinessTripInput) -> dict[str, Any]:
    year, month = _parse_month_key(month_key)
    config = load_config()
    month_data = _get_or_build_month(month_key)

    found = False
    for d in month_data["days"]:
        if d["date"] == body.date:
            d["biz_destination"] = body.destination.strip()
            d["km_city"] = max(0, int(body.km_city))
            d["km_other"] = max(0, int(body.km_other))
            d["reason"] = body.reason.strip()
            d["project"] = body.project
            d["append_commute"] = body.append_commute
            d["recompute_business"] = True
            d["locked"] = True
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"Datum {body.date} nenalezeno v měsíci {month_key}.")

    all_trips, _ = load_all_csv_trips()
    daily_csv = aggregate_trips_by_day(all_trips)
    month_data["days"] = reconcile_month_days(
        year=year,
        month=month,
        daily_csv=daily_csv,
        existing_days=month_data["days"],
        config=config,
    )
    save_month(month_key, month_data)
    summary = compute_month_summary(month_data, config)
    return {"month_data": month_data, "summary": summary}


@app.post("/api/month/{month_key}/day/{day_num}/clear-business")
def api_clear_business_trip(month_key: str, day_num: int) -> dict[str, Any]:
    year, month = _parse_month_key(month_key)
    config = load_config()
    month_data = _get_or_build_month(month_key)

    for d in month_data["days"]:
        if int(d["day"]) == day_num:
            d["km_city"] = 0
            d["km_other"] = 0
            d["biz_destination"] = ""
            d["destination"] = ""
            d["reason"] = ""
            d["project"] = ""
            d["append_commute"] = False
            d["locked"] = False
            break

    all_trips, _ = load_all_csv_trips()
    daily_csv = aggregate_trips_by_day(all_trips)
    month_data["days"] = reconcile_month_days(
        year=year,
        month=month,
        daily_csv=daily_csv,
        existing_days=month_data["days"],
        config=config,
        force_reestimate=False,
    )
    save_month(month_key, month_data)
    summary = compute_month_summary(month_data, config)
    return {"month_data": month_data, "summary": summary}


@app.post("/api/upload-csv")
async def api_upload_csv(file: UploadFile = File(...), active_month: str = "2026-09") -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Nahrajte prosím soubor s příponou .csv z aplikace MyŠkoda.")
    ensure_dirs()
    safe_name = Path(file.filename).name
    dest = CSV_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)

    all_trips, csv_files = load_all_csv_trips()
    daily_csv = aggregate_trips_by_day(all_trips)
    csv_months = get_available_csv_months(daily_csv)

    month_data = _get_or_build_month(active_month)
    config = load_config()
    summary = compute_month_summary(month_data, config)
    return {
        "csv_files": csv_files,
        "csv_months": csv_months,
        "month_data": month_data,
        "summary": summary,
    }


class RouteDistanceRequest(BaseModel):
    origin: str
    destination: str
    waypoints: list[str] = []
    round_trip: bool = True


@app.post("/api/route-distance")
async def api_route_distance(req: RouteDistanceRequest) -> dict[str, Any]:
    try:
        return await calculate_route_distance(
            origin=req.origin,
            destination=req.destination,
            waypoints=req.waypoints,
            round_trip=req.round_trip,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _attach_receipt_result(
    month_key: str,
    month_data: dict[str, Any],
    result: dict[str, Any],
    force_attach_here: bool = False,
) -> None:
    receipt_meta = result["receipt"]
    fuel_items = result["fuel_entries"]
    other_items = result["other_entries"]

    if receipt_meta["month_mismatch"] and not force_attach_here:
        # Store in pending_receipts so user can move to detected_month or keep here with 1 click
        month_data.setdefault("pending_receipts", []).append(
            {
                "receipt": receipt_meta,
                "fuel_entries": fuel_items,
                "other_entries": other_items,
            }
        )
        return

    month_data.setdefault("uploaded_receipts", []).append(receipt_meta)
    month_data.setdefault("fuel_receipts", []).extend(fuel_items)
    month_data.setdefault("other_expenses", []).extend(other_items)
    curr = receipt_meta.get("currency", "CZK")
    if curr != "CZK" and receipt_meta.get("exchange_rate"):
        month_data.setdefault("exchange_rates", {})[curr] = receipt_meta["exchange_rate"]


@app.get("/api/receipt-files")
def api_list_receipt_files() -> dict[str, Any]:
    index_data = sync_receipts_index_with_inputs()
    return {"receipt_files": _sorted_receipt_files(index_data)}


@app.post("/api/receipt-files/{filename}/extract")
async def api_extract_single_receipt_file(filename: str, active_month: str = "2026-09") -> dict[str, Any]:
    _parse_month_key(active_month)
    safe_name = Path(filename).name
    target = INPUTS_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Soubor {safe_name} nebyl ve složce Vstupy nalezen.")

    config = load_config()
    api_key = get_gemini_api_key(config)
    index_data = sync_receipts_index_with_inputs()
    prev_entry = index_data.get(safe_name, {})
    prev_month = prev_entry.get("assigned_month")

    try:
        new_entry = await extract_receipt_to_entry(
            safe_name,
            target.read_bytes(),
            fallback_month=active_month,
            api_key=api_key,
        )
        index_data[safe_name] = new_entry
        save_receipts_index(index_data)
        _sync_receipt_entry_to_months(safe_name, new_entry, prev_month=prev_month)
    except Exception as exc:
        err_msg = str(exc) or type(exc).__name__
        inferred = infer_date_from_filename(safe_name) or ""
        err_entry = {
            "filename": safe_name,
            "size_bytes": target.stat().st_size,
            "mime_type": guess_mime_type(safe_name),
            "status": "error",
            "receipt_date": prev_entry.get("receipt_date") or inferred,
            "assigned_month": prev_entry.get("assigned_month") or (inferred[:7] if inferred else active_month),
            "merchant": prev_entry.get("merchant") or "",
            "currency": prev_entry.get("currency") or "CZK",
            "exchange_rate": prev_entry.get("exchange_rate") or 1.0,
            "fuel_items": prev_entry.get("fuel_items") or [],
            "other_items": prev_entry.get("other_items") or [],
            "error_message": err_msg,
            "model_used": "",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        index_data[safe_name] = err_entry
        save_receipts_index(index_data)
        new_entry = err_entry

    month_data = _get_or_build_month(active_month)
    summary = compute_month_summary(month_data, config)
    return {
        "file_entry": new_entry,
        "receipt_files": _sorted_receipt_files(index_data),
        "month_data": month_data,
        "summary": summary,
    }


class ManualFuelItem(BaseModel):
    id: str = ""
    description: str = "Nafta"
    liters: float = 0.0
    unit_price: float = 0.0


class ManualOtherItem(BaseModel):
    id: str = ""
    description: str = ""
    amount: float = 0.0
    project: str = ""


class ManualReceiptPayload(BaseModel):
    assigned_month: str
    receipt_date: str = ""
    merchant: str = ""
    currency: str = "CZK"
    exchange_rate: float = 1.0
    fuel_items: list[ManualFuelItem] = []
    other_items: list[ManualOtherItem] = []


@app.put("/api/receipt-files/{filename}/manual")
def api_save_manual_receipt_file(
    filename: str,
    payload: ManualReceiptPayload,
    active_month: str = "2026-09",
) -> dict[str, Any]:
    _parse_month_key(active_month)
    _parse_month_key(payload.assigned_month)
    safe_name = Path(filename).name
    target = INPUTS_DIR / safe_name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Soubor {safe_name} nebyl ve složce Vstupy nalezen.")

    config = load_config()
    index_data = sync_receipts_index_with_inputs()
    prev_month = (index_data.get(safe_name) or {}).get("assigned_month")

    curr = (payload.currency or "CZK").upper().strip()
    rate = 1.0 if curr == "CZK" else float(payload.exchange_rate or 1.0)

    entry = {
        "filename": safe_name,
        "size_bytes": target.stat().st_size,
        "mime_type": guess_mime_type(safe_name),
        "status": "manual",
        "receipt_date": payload.receipt_date.strip(),
        "assigned_month": payload.assigned_month.strip(),
        "merchant": payload.merchant.strip(),
        "currency": curr,
        "exchange_rate": rate,
        "fuel_items": [item.model_dump() for item in payload.fuel_items if item.liters > 0 or item.unit_price > 0],
        "other_items": [item.model_dump() for item in payload.other_items if item.amount > 0],
        "error_message": "",
        "model_used": "manual",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    index_data[safe_name] = entry
    save_receipts_index(index_data)
    _sync_receipt_entry_to_months(safe_name, entry, prev_month=prev_month)

    month_data = _get_or_build_month(active_month)
    summary = compute_month_summary(month_data, config)
    return {
        "file_entry": entry,
        "receipt_files": _sorted_receipt_files(index_data),
        "month_data": month_data,
        "summary": summary,
    }


@app.post("/api/month/{month_key}/receipts/upload")
async def api_upload_receipts(
    month_key: str,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    _parse_month_key(month_key)
    config = load_config()
    api_key = get_gemini_api_key(config)
    index_data = sync_receipts_index_with_inputs()

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for f in files:
        fname = Path(f.filename or "uctenka.pdf").name
        if Path(fname).suffix.lower() not in ALLOWED_RECEIPT_EXTS:
            errors.append(f"{fname}: nepodporovaný formát (povoleno PDF, JPG, PNG, WEBP).")
            continue
        content = await f.read()
        INPUTS_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = INPUTS_DIR / fname
        dest_path.write_bytes(content)

        prev_month = (index_data.get(fname) or {}).get("assigned_month")
        try:
            entry = await extract_receipt_to_entry(
                fname,
                content,
                fallback_month=month_key,
                api_key=api_key,
            )
            index_data[fname] = entry
            save_receipts_index(index_data)
            _sync_receipt_entry_to_months(fname, entry, prev_month=prev_month)
            results.append(entry)
        except Exception as exc:
            err_msg = str(exc) or type(exc).__name__
            inferred = infer_date_from_filename(fname) or ""
            err_entry = {
                "filename": fname,
                "size_bytes": len(content),
                "mime_type": guess_mime_type(fname),
                "status": "error",
                "receipt_date": inferred,
                "assigned_month": inferred[:7] if inferred else month_key,
                "merchant": "",
                "currency": "CZK",
                "exchange_rate": 1.0,
                "fuel_items": [],
                "other_items": [],
                "error_message": err_msg,
                "model_used": "",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            index_data[fname] = err_entry
            save_receipts_index(index_data)
            errors.append(f"{fname}: {err_msg}")

    month_data = _get_or_build_month(month_key)
    summary = compute_month_summary(month_data, config)
    return {
        "processed": results,
        "errors": errors,
        "receipt_files": _sorted_receipt_files(index_data),
        "month_data": month_data,
        "summary": summary,
    }


class ProcessInputReceiptRequest(BaseModel):
    filename: str


@app.post("/api/month/{month_key}/receipts/from-input")
async def api_process_input_receipt(month_key: str, req: ProcessInputReceiptRequest) -> dict[str, Any]:
    _parse_month_key(month_key)
    config = load_config()
    api_key = get_gemini_api_key(config)
    index_data = sync_receipts_index_with_inputs()

    if req.filename == "__all__":
        targets = [
            INPUTS_DIR / f["filename"]
            for f in _sorted_receipt_files(index_data)
            if f.get("status") in ("unprocessed", "error")
        ]
    else:
        safe_name = Path(req.filename).name
        target = INPUTS_DIR / safe_name
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Soubor {safe_name} nebyl ve složce Vstupy nalezen.")
        targets = [target]

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in targets:
        prev_entry = index_data.get(path.name, {})
        prev_month = prev_entry.get("assigned_month")
        try:
            entry = await extract_receipt_to_entry(
                path.name,
                path.read_bytes(),
                fallback_month=month_key,
                api_key=api_key,
            )
            index_data[path.name] = entry
            save_receipts_index(index_data)
            _sync_receipt_entry_to_months(path.name, entry, prev_month=prev_month)
            results.append(entry)
        except Exception as exc:
            err_msg = str(exc) or type(exc).__name__
            inferred = infer_date_from_filename(path.name) or ""
            index_data[path.name] = {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "mime_type": guess_mime_type(path.name),
                "status": "error",
                "receipt_date": prev_entry.get("receipt_date") or inferred,
                "assigned_month": prev_entry.get("assigned_month") or (inferred[:7] if inferred else month_key),
                "merchant": prev_entry.get("merchant") or "",
                "currency": prev_entry.get("currency") or "CZK",
                "exchange_rate": prev_entry.get("exchange_rate") or 1.0,
                "fuel_items": prev_entry.get("fuel_items") or [],
                "other_items": prev_entry.get("other_items") or [],
                "error_message": err_msg,
                "model_used": "",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_receipts_index(index_data)
            errors.append(f"{path.name}: {err_msg}")

    month_data = _get_or_build_month(month_key)
    summary = compute_month_summary(month_data, config)
    return {
        "processed": results,
        "errors": errors,
        "receipt_files": _sorted_receipt_files(index_data),
        "month_data": month_data,
        "summary": summary,
    }


class ResolvePendingReceiptRequest(BaseModel):
    receipt_id: str
    action: str  # "move" | "keep" | "discard"
    target_month: str | None = None


@app.post("/api/month/{month_key}/receipts/resolve-pending")
def api_resolve_pending_receipt(month_key: str, req: ResolvePendingReceiptRequest) -> dict[str, Any]:
    _parse_month_key(month_key)
    config = load_config()
    month_data = _get_or_build_month(month_key)

    pending_list = month_data.get("pending_receipts", [])
    item = next((p for p in pending_list if p["receipt"]["id"] == req.receipt_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Čekající účtenka nebyla nalezena.")

    month_data["pending_receipts"] = [p for p in pending_list if p["receipt"]["id"] != req.receipt_id]

    if req.action == "keep":
        item["receipt"]["month_mismatch"] = False
        _attach_receipt_result(month_key, month_data, item, force_attach_here=True)
    elif req.action == "move":
        dest_month = req.target_month or item["receipt"].get("detected_month") or month_key
        _parse_month_key(dest_month)
        dest_data = _get_or_build_month(dest_month)
        item["receipt"]["month_mismatch"] = False
        _attach_receipt_result(dest_month, dest_data, item, force_attach_here=True)
        save_month(dest_month, dest_data)

    save_month(month_key, month_data)
    summary = compute_month_summary(month_data, config)
    return {"month_data": month_data, "summary": summary}


@app.get("/api/receipts/file/{stored_file}")
def api_get_receipt_file(stored_file: str) -> FileResponse:
    safe_name = Path(stored_file).name
    p = RECEIPTS_DIR / safe_name
    if not p.exists():
        p = INPUTS_DIR / safe_name
    if not p.exists():
        raise HTTPException(status_code=404, detail="Soubor účtenky nenalezen.")
    return FileResponse(
        p,
        media_type=guess_mime_type(safe_name),
        filename=safe_name,
        content_disposition_type="inline",
    )


@app.get("/api/inputs/file/{filename}")
def api_get_input_file(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    p = INPUTS_DIR / safe_name
    if not p.exists():
        raise HTTPException(status_code=404, detail="Vstupní soubor nenalezen.")
    return FileResponse(
        p,
        media_type=guess_mime_type(safe_name),
        filename=safe_name,
        content_disposition_type="inline",
    )


@app.get("/api/cnb-rate")
async def api_cnb_rate(currency: str = "EUR", date: str | None = None) -> dict[str, Any]:
    rate = await fetch_cnb_exchange_rate(currency, date)
    return {"currency": currency.upper(), "date": date, "rate": rate}


@app.put("/api/config")
def api_save_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    saved = save_config(payload)
    return {"config": saved, "gemini_key_configured": bool(get_gemini_api_key(saved))}


class TestGeminiRequest(BaseModel):
    api_key: str = ""


@app.post("/api/config/test-gemini")
async def api_test_gemini(req: TestGeminiRequest) -> dict[str, Any]:
    config = load_config()
    key = req.api_key.strip() or get_gemini_api_key(config)
    return await test_gemini_api_key(key)


@app.post("/api/export-excel/{month_key}")
def api_generate_excel(month_key: str) -> dict[str, Any]:
    _parse_month_key(month_key)
    config = load_config()
    month_data = _get_or_build_month(month_key)
    try:
        out_path = export_travel_book_excel(
            active_month_key=month_key,
            active_month_data=month_data,
            config=config,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export do Excelu selhal: {exc}") from exc
    return {
        "ok": True,
        "output_path": str(out_path),
        "filename": out_path.name,
        "download_url": f"/api/export-excel/{month_key}/download",
    }


@app.get("/api/export-excel/{month_key}/download")
def api_download_excel(month_key: str) -> FileResponse:
    _parse_month_key(month_key)
    config = load_config()
    month_data = _get_or_build_month(month_key)
    out_path = export_travel_book_excel(
        active_month_key=month_key,
        active_month_data=month_data,
        config=config,
    )
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
    )
