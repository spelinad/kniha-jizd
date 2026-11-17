from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree as ET

from .storage import (
    OUTPUTS_DIR,
    list_saved_months,
    load_config,
    load_month,
    resolve_project_path,
)

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def get_statutory_fuel_price(fuel_type: str, month_num: int) -> float:
    ft = (fuel_type or "nafta").strip().lower()
    if ft == "elektřina":
        return 7.2
    if ft == "natural":
        return 41.8 if month_num >= 10 else 34.7
    # default nafta
    return 44.5 if month_num >= 6 else 34.1


def compute_month_summary(month_data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    profile = config.get("profile", {})
    month_key = month_data.get("month", "2026-09")
    month_num = int(month_key.split("-")[1])

    days = month_data.get("days", [])
    km_city = sum(int(d.get("km_city", 0) or 0) for d in days)
    km_other = sum(int(d.get("km_other", 0) or 0) for d in days)
    km_private = sum(int(d.get("km_private", 0) or 0) for d in days)
    km_total = km_city + km_other + km_private
    km_business = km_city + km_other

    csv_total_km = sum(int(d.get("csv_km", 0) or 0) for d in days)
    active_csv_days = [d for d in days if int(d.get("csv_km", 0) or 0) > 0]
    csv_start_odo = (
        active_csv_days[0]["csv_start_odo"] - int(active_csv_days[0].get("odo_gap_km", 0) or 0)
        if active_csv_days and active_csv_days[0].get("csv_start_odo") is not None
        else None
    )
    csv_end_odo = (
        active_csv_days[-1]["csv_end_odo"]
        if active_csv_days and active_csv_days[-1].get("csv_end_odo") is not None
        else None
    )

    # Fuel receipts
    rates = {"CZK": 1.0, "EUR": 25.0, "PLN": 5.8, "HUF": 0.062, "HRK": 3.3}
    for k, v in (month_data.get("exchange_rates") or {}).items():
        try:
            rates[k.upper()] = float(v)
        except (TypeError, ValueError):
            pass

    total_liters = 0.0
    total_fuel_czk = 0.0
    for fr in month_data.get("fuel_receipts", []):
        liters = float(fr.get("liters", 0) or 0)
        unit_price = float(fr.get("unit_price", 0) or 0)
        curr = (fr.get("currency") or "CZK").upper().strip()
        rate = float(fr.get("exchange_rate") or rates.get(curr, 1.0))
        if curr != "CZK" and fr.get("exchange_rate"):
            rates[curr] = rate
        total_liters += liters
        total_fuel_czk += liters * unit_price * rate

    avg_fuel_price = round(total_fuel_czk / total_liters, 2) if total_liters > 0 else 0.0
    statutory_price = get_statutory_fuel_price(profile.get("fuel_type", "nafta"), month_num)
    effective_fuel_price = avg_fuel_price if avg_fuel_price > 0 else statutory_price

    # Other expenses
    other_czk_total = 0.0
    for oe in month_data.get("other_expenses", []):
        amount = float(oe.get("amount", 0) or 0)
        curr = (oe.get("currency") or "CZK").upper().strip()
        rate = 1.0 if curr == "CZK" else float(oe.get("exchange_rate") or rates.get(curr, 1.0))
        other_czk_total += amount * rate

    cons_city = float(profile.get("consumption_city", 5.0) or 0.0)
    cons_comb = float(profile.get("consumption_combined", 5.0) or 0.0)

    business_share = (km_business / km_total) if km_total > 0 else 0.0
    fuel_reimbursement = ((km_city * cons_city + km_other * cons_comb) / 100.0) * effective_fuel_price
    other_reimbursement = other_czk_total * business_share
    total_reimbursement = fuel_reimbursement + other_reimbursement
    estimated_liters_consumed = (
        km_city / 100.0 * cons_city + (km_other + km_private) / 100.0 * cons_comb
    )

    counts = {
        "auto": sum(1 for d in days if d.get("status") == "auto"),
        "ok": sum(1 for d in days if d.get("status") == "ok"),
        "business": sum(1 for d in days if d.get("status") == "business"),
        "review": sum(1 for d in days if d.get("status") == "review"),
        "needs_input": sum(1 for d in days if d.get("status") == "needs_input"),
        "warning": sum(1 for d in days if d.get("status") == "warning"),
        "empty": sum(1 for d in days if d.get("status") == "empty"),
    }

    return {
        "month": month_key,
        "km_city": km_city,
        "km_other": km_other,
        "km_private": km_private,
        "km_business": km_business,
        "km_total": km_total,
        "csv_total_km": csv_total_km,
        "csv_start_odo": csv_start_odo,
        "csv_end_odo": csv_end_odo,
        "odo_matches_csv": (csv_total_km == 0 or km_total == csv_total_km),
        "total_liters_refueled": round(total_liters, 2),
        "total_fuel_czk": round(total_fuel_czk, 2),
        "avg_fuel_price": avg_fuel_price,
        "statutory_fuel_price": statutory_price,
        "effective_fuel_price": effective_fuel_price,
        "other_expenses_czk": round(other_czk_total, 2),
        "business_share": round(business_share, 4),
        "business_share_pct": round(business_share * 100, 1),
        "psa_fuel_czk": round(fuel_reimbursement, 2),
        "psa_other_czk": round(other_reimbursement, 2),
        "psa_total_czk": round(total_reimbursement, 2),
        "estimated_liters_consumed": round(estimated_liters_consumed, 2),
        "status_counts": counts,
        "attention_count": counts["review"] + counts["needs_input"] + counts["warning"],
    }


class SharedStringsTable:
    """Preserves existing sharedStrings indices and appends new unique strings."""

    def __init__(self, xml_bytes: bytes) -> None:
        self.root = ET.fromstring(xml_bytes)
        self.strings: list[str] = []
        self.index_map: dict[str, int] = {}

        for idx, si in enumerate(self.root):
            parts = [t.text or "" for t in si.iter(f"{{{MAIN_NS}}}t")]
            val = "".join(parts)
            self.strings.append(val)
            if val not in self.index_map:
                self.index_map[val] = idx

        try:
            self.total_count = int(self.root.attrib.get("count", len(self.strings)))
        except ValueError:
            self.total_count = len(self.strings)

    def get_or_add(self, text: str) -> int:
        self.total_count += 1
        if text in self.index_map:
            return self.index_map[text]
        idx = len(self.strings)
        si = ET.SubElement(self.root, f"{{{MAIN_NS}}}si")
        t = ET.SubElement(si, f"{{{MAIN_NS}}}t")
        if text != text.strip() or "\n" in text:
            t.attrib[f"{{{XML_NS}}}space"] = "preserve"
        t.text = text
        self.strings.append(text)
        self.index_map[text] = idx
        return idx

    def to_bytes(self) -> bytes:
        self.root.attrib["count"] = str(self.total_count)
        self.root.attrib["uniqueCount"] = str(len(self.strings))
        return ET.tostring(self.root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _find_or_create_cell(sheet_root: ET._Element, cell_ref: str) -> ET._Element:
    row_num_str = "".join(ch for ch in cell_ref if ch.isdigit())
    row = sheet_root.find(f".//{{{MAIN_NS}}}row[@r='{row_num_str}']")
    if row is None:
        sheet_data = sheet_root.find(f"{{{MAIN_NS}}}sheetData")
        row = ET.SubElement(sheet_data, f"{{{MAIN_NS}}}row", r=row_num_str)
    for c in row:
        if c.attrib.get("r") == cell_ref:
            return c
    return ET.SubElement(row, f"{{{MAIN_NS}}}c", r=cell_ref)


def _set_cell_value(
    sheet_root: ET._Element,
    cell_ref: str,
    value: Any,
    shared_strings: SharedStringsTable,
) -> None:
    cell = _find_or_create_cell(sheet_root, cell_ref)
    # Never overwrite a formula cell!
    if cell.find(f"{{{MAIN_NS}}}f") is not None:
        return

    # Remove existing value child
    existing_v = cell.find(f"{{{MAIN_NS}}}v")
    if existing_v is not None:
        cell.remove(existing_v)
    existing_is = cell.find(f"{{{MAIN_NS}}}is")
    if existing_is is not None:
        cell.remove(existing_is)

    if value is None or value == "":
        cell.attrib.pop("t", None)
        return

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        cell.attrib.pop("t", None)
        v_el = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
        v_el.text = str(value)
        return

    text = str(value)
    ss_idx = shared_strings.get_or_add(text)
    cell.attrib["t"] = "s"
    v_el = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
    v_el.text = str(ss_idx)


def _write_title_sheet(
    sheet_root: ET._Element,
    profile: dict[str, Any],
    shared_strings: SharedStringsTable,
) -> None:
    dept_raw = str(profile.get("department", "")).strip()
    dept_val: Any = int(dept_raw) if dept_raw.isdigit() else dept_raw

    mappings = {
        "B2": profile.get("vehicle_type", "Manažerský "),
        "B4": profile.get("license_plate", ""),
        "B6": float(profile.get("consumption_city", 5.0) or 0),
        "B8": float(profile.get("consumption_combined", 5.0) or 0),
        "B10": int(profile.get("initial_odometer", 0) or 0),
        "B12": profile.get("fuel_type", "nafta"),
        "B14": profile.get("driver_name", ""),
        "B16": profile.get("staff_number", ""),
        "B18": dept_val,
    }
    for ref, val in mappings.items():
        _set_cell_value(sheet_root, ref, val, shared_strings)


def _write_month_sheet(
    sheet_root: ET._Element,
    month_data: dict[str, Any],
    shared_strings: SharedStringsTable,
) -> None:
    days_by_num = {int(d["day"]): d for d in month_data.get("days", []) if "day" in d}

    # 1. Daily rows 13..43 (days 1..31)
    for day_num in range(1, 32):
        r = 12 + day_num
        d = days_by_num.get(day_num)
        if not d:
            continue
        km_city = int(d.get("km_city", 0) or 0)
        km_other = int(d.get("km_other", 0) or 0)
        km_private = int(d.get("km_private", 0) or 0)

        _set_cell_value(sheet_root, f"B{r}", d.get("destination") or "", shared_strings)
        _set_cell_value(sheet_root, f"C{r}", km_city if km_city > 0 else "", shared_strings)
        _set_cell_value(sheet_root, f"D{r}", km_other if km_other > 0 else "", shared_strings)
        _set_cell_value(sheet_root, f"E{r}", km_private if km_private > 0 else "", shared_strings)
        _set_cell_value(sheet_root, f"G{r}", d.get("reason") or "", shared_strings)
        _set_cell_value(sheet_root, f"H{r}", d.get("project") or "", shared_strings)

    # Collect custom exchange rates used by receipts
    fx_rates: dict[str, float] = {}
    for k, v in (month_data.get("exchange_rates") or {}).items():
        try:
            fx_rates[k.upper()] = float(v)
        except (TypeError, ValueError):
            pass

    # 2. Fuel receipts rows 22..37 (up to 16 receipts)
    fuel_receipts = month_data.get("fuel_receipts", [])[:16]
    for idx in range(16):
        r = 22 + idx
        if idx < len(fuel_receipts):
            fr = fuel_receipts[idx]
            curr = (fr.get("currency") or "CZK").upper().strip()
            liters = float(fr.get("liters", 0) or 0)
            unit_price = float(fr.get("unit_price", 0) or 0)
            if curr != "CZK" and fr.get("exchange_rate"):
                fx_rates[curr] = float(fr["exchange_rate"])
            _set_cell_value(sheet_root, f"L{r}", round(liters, 2) if liters > 0 else "", shared_strings)
            _set_cell_value(sheet_root, f"M{r}", curr, shared_strings)
            _set_cell_value(sheet_root, f"N{r}", round(unit_price, 2) if unit_price > 0 else "", shared_strings)
        else:
            _set_cell_value(sheet_root, f"L{r}", "", shared_strings)
            _set_cell_value(sheet_root, f"M{r}", "CZK", shared_strings)
            _set_cell_value(sheet_root, f"N{r}", "", shared_strings)

    # 3. Other expenses: CZK (rows 46..52, up to 7) and foreign currency (rows 46..51 + 54..59, up to 6)
    other_all = month_data.get("other_expenses", [])
    other_czk = [o for o in other_all if (o.get("currency") or "CZK").upper().strip() == "CZK"][:7]
    other_fx = [o for o in other_all if (o.get("currency") or "CZK").upper().strip() != "CZK"][:6]

    for idx in range(7):
        r = 46 + idx
        if idx < len(other_czk):
            oe = other_czk[idx]
            amt = float(oe.get("amount", 0) or 0)
            _set_cell_value(sheet_root, f"B{r}", oe.get("description") or "", shared_strings)
            _set_cell_value(sheet_root, f"E{r}", round(amt, 2) if amt > 0 else "", shared_strings)
            _set_cell_value(sheet_root, f"H{r}", oe.get("project") or "", shared_strings)
        else:
            _set_cell_value(sheet_root, f"B{r}", "", shared_strings)
            _set_cell_value(sheet_root, f"E{r}", "", shared_strings)
            _set_cell_value(sheet_root, f"H{r}", "", shared_strings)

    for idx in range(6):
        r_fx = 46 + idx
        r_desc = 54 + idx
        if idx < len(other_fx):
            oe = other_fx[idx]
            curr = (oe.get("currency") or "EUR").upper().strip()
            amt = float(oe.get("amount", 0) or 0)
            if oe.get("exchange_rate"):
                fx_rates[curr] = float(oe["exchange_rate"])
            _set_cell_value(sheet_root, f"L{r_fx}", curr, shared_strings)
            _set_cell_value(sheet_root, f"M{r_fx}", round(amt, 2) if amt > 0 else "", shared_strings)
            _set_cell_value(sheet_root, f"B{r_desc}", oe.get("description") or "", shared_strings)
            _set_cell_value(sheet_root, f"H{r_desc}", oe.get("project") or "", shared_strings)
        else:
            _set_cell_value(sheet_root, f"L{r_fx}", "EUR", shared_strings)
            _set_cell_value(sheet_root, f"M{r_fx}", "", shared_strings)
            _set_cell_value(sheet_root, f"B{r_desc}", "", shared_strings)
            _set_cell_value(sheet_root, f"H{r_desc}", "", shared_strings)

    # 4. Write CNB FX rates into S22 (EUR), S23 (PLN), S24 (HUF), S25 (HRK) if used
    fx_row_map = {"EUR": "S22", "PLN": "S23", "HUF": "S24", "HRK": "S25"}
    for curr, cell_ref in fx_row_map.items():
        if curr in fx_rates and fx_rates[curr] > 0:
            _set_cell_value(sheet_root, cell_ref, round(fx_rates[curr], 4), shared_strings)


def export_travel_book_excel(
    active_month_key: str | None = None,
    active_month_data: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> Path:
    """Losslessly populate Travel_book_2026_CZ_1.10.2026.xlsx preserving labels, VML, comments, and formulas."""
    if config is None:
        config = load_config()
    profile = config.get("profile", {})

    template_path = resolve_project_path(
        profile.get("template_path") or "Vstupy/Travel_book_2026_CZ_1.10.2026.xlsx"
    )
    if not template_path.exists():
        raise FileNotFoundError(f"Šablona Excel nebyla nalezena: {template_path}")

    raw_out = profile.get("output_path") or f"Vystupy/Travel_book_{profile.get('year', 2026)}_CZ_{profile.get('license_plate', 'export')}.xlsx"
    output_path = resolve_project_path(raw_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # Collect all saved months + active month override
    months_to_write: dict[str, dict[str, Any]] = {}
    for mk in list_saved_months():
        mdata = load_month(mk)
        if mdata:
            months_to_write[mk] = mdata
    if active_month_key and active_month_data:
        months_to_write[active_month_key] = active_month_data

    with zipfile.ZipFile(template_path, "r") as zin:
        shared_strings = SharedStringsTable(zin.read("xl/sharedStrings.xml"))
        wb_root = ET.fromstring(zin.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zin.read("xl/_rels/workbook.xml.rels"))
        ct_root = ET.fromstring(zin.read("[Content_Types].xml"))

        # Map sheet name -> zip entry path
        rel_map: dict[str, str] = {}
        for rel in rels_root:
            rel_map[rel.attrib["Id"]] = rel.attrib["Target"]

        sheet_targets: dict[str, str] = {}
        for sh in wb_root.findall(f".//{{{MAIN_NS}}}sheet"):
            name = sh.attrib.get("name", "")
            rid = sh.attrib.get(f"{{{REL_NS}}}id", "")
            if rid in rel_map:
                target = rel_map[rid].lstrip("/")
                if not target.startswith("xl/"):
                    target = f"xl/{target}"
                sheet_targets[name] = target

        modified_entries: dict[str, bytes] = {}

        # Update TITLE sheet
        if "TITLE" in sheet_targets:
            title_entry = sheet_targets["TITLE"]
            title_root = ET.fromstring(zin.read(title_entry))
            _write_title_sheet(title_root, profile, shared_strings)
            modified_entries[title_entry] = ET.tostring(
                title_root, xml_declaration=True, encoding="UTF-8", standalone=True
            )

        # Update each populated KJ YYYY-MM sheet
        for m_key, m_data in months_to_write.items():
            sheet_name = f"KJ {m_key}"
            if sheet_name in sheet_targets:
                entry = sheet_targets[sheet_name]
                s_root = ET.fromstring(zin.read(entry))
                _write_month_sheet(s_root, m_data, shared_strings)
                modified_entries[entry] = ET.tostring(
                    s_root, xml_declaration=True, encoding="UTF-8", standalone=True
                )

        # Force full formula recalculation on open & drop stale calcChain cleanly
        calc_pr = wb_root.find(f"{{{MAIN_NS}}}calcPr")
        if calc_pr is None:
            calc_pr = ET.SubElement(wb_root, f"{{{MAIN_NS}}}calcPr")
        calc_pr.attrib["fullCalcOnLoad"] = "1"
        modified_entries["xl/workbook.xml"] = ET.tostring(
            wb_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        modified_entries["xl/sharedStrings.xml"] = shared_strings.to_bytes()

        # Remove calcChain rel & content-type override so Excel regenerates it cleanly
        for rel in list(rels_root):
            if rel.attrib.get("Target", "").endswith("calcChain.xml"):
                rels_root.remove(rel)
        modified_entries["xl/_rels/workbook.xml.rels"] = ET.tostring(
            rels_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )

        for ov in list(ct_root):
            if ov.attrib.get("PartName") == "/xl/calcChain.xml":
                ct_root.remove(ov)
        modified_entries["[Content_Types].xml"] = ET.tostring(
            ct_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "xl/calcChain.xml":
                    continue
                if item.filename in modified_entries:
                    zout.writestr(item.filename, modified_entries[item.filename])
                else:
                    zout.writestr(item.filename, zin.read(item.filename))

        output_path.write_bytes(buf.getvalue())

    return output_path
