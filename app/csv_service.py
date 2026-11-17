from __future__ import annotations

import calendar
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import CSV_DIR, INPUTS_DIR

CZECH_WEEKDAYS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]


def _parse_int(val: str | None) -> int:
    if val is None or str(val).strip() == "":
        return 0
    return int(round(float(str(val).replace(",", ".").strip())))


def _parse_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    return float(str(val).replace(",", ".").strip())


def parse_skoda_csv_file(path: Path) -> list[dict[str, Any]]:
    trips: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            end_raw = (row.get("End of trip") or "").strip()
            if not end_raw:
                continue
            dt = datetime.strptime(end_raw, "%d.%m.%Y %H:%M")
            start_km = _parse_int(row.get("Start mileage in km"))
            end_km = _parse_int(row.get("End mileage in km"))
            mileage_km = _parse_int(row.get("Mileage in km"))
            duration_min = _parse_int(row.get("Travel time in minutes"))
            avg_speed = _parse_int(row.get("Average speed in km/h"))
            avg_cons = _parse_float(row.get("Average fuel consumption in l/100km"))
            trips.append(
                {
                    "end_dt": dt.isoformat(timespec="minutes"),
                    "end_time": dt.strftime("%H:%M"),
                    "date": dt.strftime("%Y-%m-%d"),
                    "month": dt.strftime("%Y-%m"),
                    "start_km": start_km,
                    "end_km": end_km,
                    "mileage_km": mileage_km,
                    "duration_min": duration_min,
                    "avg_speed_kmh": avg_speed,
                    "avg_consumption": avg_cons,
                    "source_file": path.name,
                }
            )
    return trips


def load_all_csv_trips() -> tuple[list[dict[str, Any]], list[str]]:
    """Load and deduplicate all Skoda tripStatistics CSV files from Vstupy/ and data/csv/."""
    csv_files: list[Path] = []
    for folder in (INPUTS_DIR, CSV_DIR):
        if folder.exists():
            csv_files.extend(sorted(folder.glob("*.csv")))

    seen: set[tuple[str, int, int]] = set()
    all_trips: list[dict[str, Any]] = []
    loaded_names: list[str] = []

    for path in csv_files:
        try:
            parsed = parse_skoda_csv_file(path)
        except Exception:
            continue
        if parsed:
            loaded_names.append(path.name)
        for t in parsed:
            key = (t["end_dt"], t["start_km"], t["end_km"])
            if key not in seen:
                seen.add(key)
                all_trips.append(t)

    all_trips.sort(key=lambda t: (t["end_dt"], t["start_km"], t["end_km"]))
    return all_trips, loaded_names


def aggregate_trips_by_day(all_trips: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate trips into daily summaries using odometer delta (end_odo - start_odo)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for t in all_trips:
        grouped.setdefault(t["date"], []).append(t)

    daily: dict[str, dict[str, Any]] = {}
    prev_end_odo: int | None = None

    for date_str in sorted(grouped.keys()):
        day_trips = sorted(grouped[date_str], key=lambda t: (t["end_dt"], t["start_km"]))
        start_odo = min(t["start_km"] for t in day_trips)
        end_odo = max(t["end_km"] for t in day_trips)
        raw_delta = max(0, end_odo - start_odo)

        gap_km = 0
        if prev_end_odo is not None and start_odo > prev_end_odo:
            gap_km = start_odo - prev_end_odo

        daily[date_str] = {
            "date": date_str,
            "month": date_str[:7],
            "trips": day_trips,
            "trip_count": len(day_trips),
            "start_odo": start_odo,
            "end_odo": end_odo,
            "raw_odo_km": raw_delta,
            "gap_km": gap_km,
            "total_odo_km": raw_delta + gap_km,
            "rounded_sum_km": sum(t["mileage_km"] for t in day_trips),
        }
        prev_end_odo = end_odo

    return daily


def get_available_csv_months(daily: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    months: dict[str, dict[str, Any]] = {}
    for d in sorted(daily.values(), key=lambda x: x["date"]):
        m = d["month"]
        if m not in months:
            months[m] = {
                "month": m,
                "active_days": 0,
                "total_km": 0,
                "start_odo": d["start_odo"] - d["gap_km"],
                "end_odo": d["end_odo"],
            }
        months[m]["active_days"] += 1
        months[m]["total_km"] += d["total_odo_km"]
        months[m]["end_odo"] = d["end_odo"]
    return months


def _build_suggestions(
    target_km: int,
    config: dict[str, Any],
    include_business: bool = True,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (best_private_destination, suggestion_chips) for a given distance."""
    private_routes = config.get("private_routes", [])
    business_routes = config.get("business_routes", [])

    scored_private: list[tuple[float, dict[str, Any], str]] = []
    for r in private_routes:
        min_km = float(r.get("min_km", 0))
        max_km = float(r.get("max_km", 0))
        typical = float(r.get("typical_km", 0) or (min_km + max_km) / 2)
        if min_km <= target_km <= max_km:
            score = abs(typical - target_km)
            scored_private.append((score, r, "range"))
        elif typical >= 80 and 0.75 * (typical / 2) <= target_km <= 1.25 * (typical / 2):
            score = abs((typical / 2) - target_km) + 15.0
            scored_private.append((score, r, "one_way"))

    scored_private.sort(key=lambda item: item[0])
    best_dest = scored_private[0][1]["destination"] if scored_private else None

    suggestions: list[dict[str, Any]] = []
    seen_labels: set[str] = set()

    for _, r, match_kind in scored_private:
        dest = r["destination"]
        if dest in seen_labels:
            continue
        seen_labels.add(dest)
        suffix = " (1 cesta)" if match_kind == "one_way" else ""
        suggestions.append(
            {
                "kind": "private",
                "id": r["id"],
                "label": f"{r['name']}{suffix}",
                "destination": dest,
            }
        )

    if include_business and target_km >= 35:
        scored_biz: list[tuple[float, dict[str, Any]]] = []
        for b in business_routes:
            b_km = int(b.get("km_city", 0)) + int(b.get("km_other", 0))
            if b_km <= 0:
                continue
            if 0.70 * b_km <= target_km <= 1.35 * b_km:
                scored_biz.append((abs(b_km - target_km), b))
        scored_biz.sort(key=lambda item: item[0])
        for _, b in scored_biz[:3]:
            b_km = int(b.get("km_city", 0)) + int(b.get("km_other", 0))
            suggestions.append(
                {
                    "kind": "business",
                    "id": b["id"],
                    "label": f"Služební: {b['name']} ({b_km} km)",
                    "destination": b["destination"],
                    "km_city": int(b.get("km_city", 0)),
                    "km_other": int(b.get("km_other", 0)),
                    "reason": b.get("reason", ""),
                    "project": b.get("project", ""),
                    "append_commute": bool(b.get("append_commute", False)),
                }
            )

    # Always append remaining private routes as quick fallback chips
    for r in private_routes:
        dest = r["destination"]
        if dest not in seen_labels:
            seen_labels.add(dest)
            suggestions.append(
                {
                    "kind": "private",
                    "id": r["id"],
                    "label": r["name"],
                    "destination": dest,
                }
            )

    return best_dest, suggestions[:8]


def _estimate_private_day(
    csv_km: int,
    weekday: int,
    config: dict[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (destination, status, suggestions) for a non-business day with csv_km > 0."""
    private_routes = config.get("private_routes", [])
    commute_route = next((r for r in private_routes if r.get("is_default_commute")), None)

    if commute_route:
        c_min = int(commute_route.get("min_km", 12))
        c_max = int(commute_route.get("max_km", 65))
        if weekday < 5 and c_min <= csv_km <= c_max:
            _, suggestions = _build_suggestions(csv_km, config, include_business=True)
            return commute_route["destination"], "auto", suggestions

    best_dest, suggestions = _build_suggestions(csv_km, config, include_business=True)
    if best_dest:
        return best_dest, "review", suggestions

    # Short drives (< 12 km) on a weekday can default to review with commute/Praha suggestions
    return "", "needs_input", suggestions


def _format_business_destination(
    biz_dest: str,
    km_private: int,
    append_commute: bool,
) -> str:
    cleaned = biz_dest.strip()
    if km_private > 0 and append_commute and "soukr." not in cleaned.lower():
        return f"{cleaned}, soukr. Středokluky"
    return cleaned


def reconcile_month_days(
    year: int,
    month: int,
    daily_csv: dict[str, dict[str, Any]],
    existing_days: list[dict[str, Any]] | None,
    config: dict[str, Any],
    force_reestimate: bool = False,
) -> list[dict[str, Any]]:
    """Build or refresh the 1..N day rows for YYYY-MM combining CSV odometer data & user entries."""
    num_days = calendar.monthrange(year, month)[1]
    existing_by_date: dict[str, dict[str, Any]] = {
        d["date"]: d for d in (existing_days or []) if "date" in d
    }

    rows: list[dict[str, Any]] = []
    for day_num in range(1, num_days + 1):
        date_obj = datetime(year, month, day_num)
        date_str = date_obj.strftime("%Y-%m-%d")
        weekday = date_obj.weekday()  # 0=Po .. 6=Ne
        csv_info = daily_csv.get(date_str)

        csv_km = int(csv_info["total_odo_km"]) if csv_info else 0
        csv_trips = csv_info["trips"] if csv_info else []
        csv_start_odo = csv_info["start_odo"] if csv_info else None
        csv_end_odo = csv_info["end_odo"] if csv_info else None
        gap_km = int(csv_info["gap_km"]) if csv_info else 0

        prev = existing_by_date.get(date_str)
        has_biz = bool(
            prev and (int(prev.get("km_city", 0) or 0) > 0 or int(prev.get("km_other", 0) or 0) > 0)
        )

        if has_biz and prev is not None:
            km_city = int(prev.get("km_city", 0) or 0)
            km_other = int(prev.get("km_other", 0) or 0)
            biz_total = km_city + km_other
            reason = prev.get("reason", "")
            project = prev.get("project", "")
            append_commute = bool(prev.get("append_commute", False))
            base_biz_dest = prev.get("biz_destination") or prev.get("destination", "")

            if prev.get("locked") and not prev.get("recompute_business") and csv_km == int(prev.get("csv_km", csv_km)):
                km_private = int(prev.get("km_private", 0) or 0)
                destination = prev.get("destination", base_biz_dest)
            else:
                km_private = max(0, csv_km - biz_total) if csv_km > 0 else 0
                destination = _format_business_destination(base_biz_dest, km_private, append_commute)

            status = "warning" if (csv_km > 0 and biz_total > csv_km) else "business"
            _, suggestions = _build_suggestions(csv_km or biz_total, config, include_business=True)
            rows.append(
                {
                    "date": date_str,
                    "day": day_num,
                    "weekday": weekday,
                    "weekday_label": CZECH_WEEKDAYS[weekday],
                    "is_weekend": weekday >= 5,
                    "csv_km": csv_km,
                    "csv_start_odo": csv_start_odo,
                    "csv_end_odo": csv_end_odo,
                    "odo_gap_km": gap_km,
                    "csv_trips": csv_trips,
                    "destination": destination,
                    "biz_destination": base_biz_dest,
                    "append_commute": append_commute,
                    "km_city": km_city,
                    "km_other": km_other,
                    "km_private": km_private,
                    "reason": reason,
                    "project": project,
                    "status": status,
                    "locked": bool(prev.get("locked", True)),
                    "suggestions": suggestions,
                }
            )
            continue

        if prev is not None and prev.get("locked") and not force_reestimate:
            # User explicitly edited/confirmed this private or empty day
            _, suggestions = _build_suggestions(csv_km, config, include_business=True)
            # If CSV data arrived after locking and csv_km differs from 0 while prev was empty:
            km_priv = int(prev.get("km_private", 0) or 0)
            if csv_km > 0 and km_priv == 0 and not prev.get("destination"):
                dest, st, suggestions = _estimate_private_day(csv_km, weekday, config)
                km_priv = csv_km
                locked = False
            else:
                dest = prev.get("destination", "")
                st = prev.get("status", "ok")
                locked = True
                if csv_km > 0 and km_priv != csv_km and int(prev.get("csv_km", 0) or 0) != csv_km:
                    km_priv = csv_km
            rows.append(
                {
                    "date": date_str,
                    "day": day_num,
                    "weekday": weekday,
                    "weekday_label": CZECH_WEEKDAYS[weekday],
                    "is_weekend": weekday >= 5,
                    "csv_km": csv_km,
                    "csv_start_odo": csv_start_odo,
                    "csv_end_odo": csv_end_odo,
                    "odo_gap_km": gap_km,
                    "csv_trips": csv_trips,
                    "destination": dest,
                    "biz_destination": "",
                    "append_commute": False,
                    "km_city": 0,
                    "km_other": 0,
                    "km_private": km_priv,
                    "reason": prev.get("reason", ""),
                    "project": prev.get("project", ""),
                    "status": st,
                    "locked": locked,
                    "suggestions": suggestions,
                }
            )
            continue

        # Fresh estimation from CSV
        if csv_km == 0:
            rows.append(
                {
                    "date": date_str,
                    "day": day_num,
                    "weekday": weekday,
                    "weekday_label": CZECH_WEEKDAYS[weekday],
                    "is_weekend": weekday >= 5,
                    "csv_km": 0,
                    "csv_start_odo": csv_start_odo,
                    "csv_end_odo": csv_end_odo,
                    "odo_gap_km": 0,
                    "csv_trips": [],
                    "destination": "",
                    "biz_destination": "",
                    "append_commute": False,
                    "km_city": 0,
                    "km_other": 0,
                    "km_private": 0,
                    "reason": "",
                    "project": "",
                    "status": "empty",
                    "locked": False,
                    "suggestions": [],
                }
            )
        else:
            dest, st, suggestions = _estimate_private_day(csv_km, weekday, config)
            rows.append(
                {
                    "date": date_str,
                    "day": day_num,
                    "weekday": weekday,
                    "weekday_label": CZECH_WEEKDAYS[weekday],
                    "is_weekend": weekday >= 5,
                    "csv_km": csv_km,
                    "csv_start_odo": csv_start_odo,
                    "csv_end_odo": csv_end_odo,
                    "odo_gap_km": gap_km,
                    "csv_trips": csv_trips,
                    "destination": dest,
                    "biz_destination": "",
                    "append_commute": False,
                    "km_city": 0,
                    "km_other": 0,
                    "km_private": csv_km,
                    "reason": "",
                    "project": "",
                    "status": st,
                    "locked": False,
                    "suggestions": suggestions,
                }
            )

    return rows
