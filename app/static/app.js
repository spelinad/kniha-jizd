const MONTH_NAMES_CS = [
  "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
  "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"
];

const state = {
  config: null,
  geminiKeyConfigured: false,
  csvMonths: {},
  savedMonths: [],
  inputReceiptFiles: [],
  receiptFiles: [],
  receiptFilter: "all",
  extractingFiles: new Set(),
  editorDraft: null,
  activeMonth: "2026-09",
  monthData: null,
  summary: null,
  dayFilter: "all",
  bizMode: "catalog",
  saveTimer: null,
};

function fmtNum(val, decimals = 2) {
  const n = Number(val || 0);
  return n.toLocaleString("cs-CZ", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtInt(val) {
  return Math.round(Number(val || 0)).toLocaleString("cs-CZ");
}

function showToast(msg, type = "info") {
  const box = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type === "error" ? "toast-error" : type === "success" ? "toast-success" : ""}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

async function apiJson(url, options = {}) {
  const resp = await fetch(url, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || `Chyba požadavku (${resp.status})`);
  }
  return data;
}

async function initApp() {
  try {
    const init = await apiJson("/api/init");
    state.config = init.config;
    state.geminiKeyConfigured = init.gemini_key_configured;
    state.csvMonths = init.csv_months || {};
    state.savedMonths = init.saved_months || [];
    state.inputReceiptFiles = init.input_receipt_files || [];
    state.receiptFiles = init.receipt_files || [];
    state.activeMonth = init.default_month || "2026-09";

    renderVehicleHeader();
    renderMonthBar();
    renderBusinessTemplates();
    renderGeminiWarning();

    await loadMonth(state.activeMonth);
  } catch (err) {
    showToast(`Chyba inicializace: ${err.message}`, "error");
  }
}

function renderVehicleHeader() {
  const p = state.config?.profile || {};
  document.getElementById("vehicle-badge").textContent =
    `RZ ${p.license_plate || "—"} · ${p.driver_name || ""} (${p.staff_number || ""}) · ${p.fuel_type || "nafta"}`;
}

function renderGeminiWarning() {
  const banner = document.getElementById("gemini-key-warning");
  if (state.geminiKeyConfigured) {
    banner.classList.add("hidden");
  } else {
    banner.classList.remove("hidden");
  }
}

function renderMonthBar() {
  const bar = document.getElementById("month-bar");
  const year = state.config?.profile?.year || 2026;
  bar.innerHTML = "";
  for (let m = 1; m <= 12; m++) {
    const mm = String(m).padStart(2, "0");
    const key = `${year}-${mm}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `month-pill ${key === state.activeMonth ? "active" : ""}`;
    const hasCsv = Boolean(state.csvMonths[key]);
    const csvKm = hasCsv ? ` (${state.csvMonths[key].total_km} km)` : "";
    btn.innerHTML = `<span class="dot ${hasCsv ? "has-csv" : ""}"></span>${mm}/${year} ${MONTH_NAMES_CS[m - 1]}${csvKm}`;
    btn.addEventListener("click", () => loadMonth(key));
    bar.appendChild(btn);
  }
}

function renderBusinessTemplates() {
  const sel = document.getElementById("biz-template");
  const current = sel.value;
  sel.innerHTML = '<option value="">— Vyberte typickou služební cestu —</option>';
  for (const r of state.config?.business_routes || []) {
    const opt = document.createElement("option");
    opt.value = r.id;
    const km = (r.km_city || 0) + (r.km_other || 0);
    opt.textContent = `${r.name} — ${km} km (${r.km_city > 0 ? "Město" : "Ostatní"})`;
    sel.appendChild(opt);
  }
  if (current) sel.value = current;
}

async function loadMonth(monthKey) {
  state.activeMonth = monthKey;
  renderMonthBar();
  try {
    const res = await apiJson(`/api/month/${monthKey}`);
    state.monthData = res.month_data;
    state.summary = res.summary;
    state.csvMonths = res.csv_months || state.csvMonths;
    document.getElementById("month-title-label").textContent = monthKey;
    renderAllMonthUI();
  } catch (err) {
    showToast(`Nepodařilo se načíst měsíc ${monthKey}: ${err.message}`, "error");
  }
}

function renderAllMonthUI() {
  renderKPIs();
  renderBizDateSelect();
  renderDaysTable();
  renderReceiptFiles();
  renderPendingReceipts();
  renderFuelReceipts();
  renderOtherExpenses();
  renderPSASummary();
}

function renderKPIs() {
  const s = state.summary;
  if (!s) return;

  if (s.csv_start_odo !== null && s.csv_end_odo !== null) {
    document.getElementById("kpi-odo").textContent = `${fmtInt(s.csv_start_odo)} → ${fmtInt(s.csv_end_odo)} km`;
    const diffOk = s.odo_matches_csv;
    document.getElementById("kpi-odo-meta").textContent = diffOk
      ? `✅ Celkem ${fmtInt(s.km_total)} km (přesná shoda se Škoda CSV)`
      : `⚠️ Zapsáno ${fmtInt(s.km_total)} km vs. CSV ${fmtInt(s.csv_total_km)} km`;
  } else {
    document.getElementById("kpi-odo").textContent = `${fmtInt(s.km_total)} km`;
    document.getElementById("kpi-odo-meta").textContent = "Pro tento měsíc zatím nejsou data ze Škoda CSV";
  }

  document.getElementById("kpi-biz").textContent = `${fmtInt(s.km_business)} km`;
  document.getElementById("kpi-biz-meta").textContent = `Město ${fmtInt(s.km_city)} km · Ostatní ${fmtInt(s.km_other)} km`;

  document.getElementById("kpi-priv").textContent = `${fmtInt(s.km_private)} km`;
  document.getElementById("kpi-priv-meta").textContent = `Podíl služebních jízd: ${fmtNum(s.business_share_pct, 1)} %`;

  document.getElementById("kpi-psa").textContent = `${fmtNum(s.psa_total_czk)} Kč`;
  document.getElementById("kpi-psa-meta").textContent =
    `FUEL ${fmtNum(s.psa_fuel_czk)} Kč · EXP ${fmtNum(s.psa_other_czk)} Kč`;
}

function renderBizDateSelect() {
  const sel = document.getElementById("biz-date");
  const prev = sel.value;
  sel.innerHTML = "";
  for (const d of state.monthData?.days || []) {
    const opt = document.createElement("option");
    opt.value = d.date;
    const dd = String(d.day).padStart(2, "0");
    const mm = state.activeMonth.split("-")[1];
    const csvInfo = d.csv_km > 0 ? ` — CSV: ${d.csv_km} km (${d.csv_trips.length} jízd)` : " — bez jízdy v CSV";
    const bizTag = (d.km_city + d.km_other) > 0 ? " [SLUŽ.]" : "";
    opt.textContent = `${dd}.${mm}. (${d.weekday_label})${csvInfo}${bizTag}`;
    sel.appendChild(opt);
  }
  if (prev && [...sel.options].some((o) => o.value === prev)) {
    sel.value = prev;
  }
}

function renderDaysTable() {
  const days = state.monthData?.days || [];
  const counts = {
    all: days.length,
    active: days.filter((d) => d.csv_km > 0 || d.km_city + d.km_other + d.km_private > 0).length,
    attention: days.filter((d) => ["review", "needs_input", "warning"].includes(d.status)).length,
  };
  document.getElementById("cnt-all").textContent = counts.all;
  document.getElementById("cnt-active").textContent = counts.active;
  document.getElementById("cnt-attention").textContent = counts.attention;

  const filtered = days.filter((d) => {
    if (state.dayFilter === "active") {
      return d.csv_km > 0 || d.km_city + d.km_other + d.km_private > 0;
    }
    if (state.dayFilter === "attention") {
      return ["review", "needs_input", "warning"].includes(d.status);
    }
    return true;
  });

  const tbody = document.getElementById("days-tbody");
  tbody.innerHTML = "";

  for (const d of filtered) {
    const tr = document.createElement("tr");
    if (d.is_weekend) tr.classList.add("is-weekend");
    if (d.status === "warning") tr.classList.add("row-warning");
    else if (d.status === "needs_input") tr.classList.add("row-needs-input");
    else if (d.status === "business") tr.classList.add("row-business");

    const dd = String(d.day).padStart(2, "0");
    const mm = state.activeMonth.split("-")[1];
    const totalDayKm = (d.km_city || 0) + (d.km_other || 0) + (d.km_private || 0);

    // Date cell
    const tdDate = document.createElement("td");
    tdDate.innerHTML = `<strong>${dd}.${mm}.</strong> <span class="muted">${d.weekday_label}</span>`;
    tr.appendChild(tdDate);

    // CSV cell
    const tdCsv = document.createElement("td");
    tdCsv.className = "csv-cell";
    if (d.csv_km > 0) {
      const tripDetail = d.csv_trips
        .map((t) => `${t.end_time} (${t.start_km}→${t.end_km}, ${t.mileage_km} km)`)
        .join("\n");
      tdCsv.title = tripDetail;
      tdCsv.innerHTML = `<strong>${d.csv_trips.length} jízd · ${d.csv_km} km</strong><div class="csv-odo">${d.csv_start_odo} → ${d.csv_end_odo} km</div>`;
    } else {
      tdCsv.innerHTML = '<span class="muted">—</span>';
    }
    tr.appendChild(tdCsv);

    // Destination cell + quick suggestion chips
    const tdDest = document.createElement("td");
    const destInput = document.createElement("input");
    destInput.type = "text";
    destInput.value = d.destination || "";
    destInput.placeholder = d.csv_km > 0 ? "Vyberte nebo napište cíl..." : "";
    destInput.addEventListener("change", () => {
      d.destination = destInput.value;
      d.locked = true;
      if (d.status === "needs_input" || d.status === "review") {
        d.status = destInput.value.trim() ? "ok" : "needs_input";
      }
      persistMonth();
    });
    tdDest.appendChild(destInput);

    if (["review", "needs_input", "warning"].includes(d.status) && (d.suggestions || []).length > 0) {
      const chips = document.createElement("div");
      chips.className = "chip-row";
      for (const s of d.suggestions) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `chip ${s.kind === "business" ? "chip-biz" : ""}`;
        chip.textContent = s.kind === "business" ? `💼 ${s.label.replace("Služební: ", "")}` : s.destination.replace(/^soukr\.\s*/i, "");
        chip.title = s.label;
        chip.addEventListener("click", () => applySuggestionToDay(d, s));
        chips.appendChild(chip);
      }
      tdDest.appendChild(chips);
    }
    tr.appendChild(tdDest);

    // Město, Ostatní, Soukr.
    tr.appendChild(makeNumCell(d, "km_city", () => onKmEdited(d)));
    tr.appendChild(makeNumCell(d, "km_other", () => onKmEdited(d)));
    tr.appendChild(makeNumCell(d, "km_private", () => onPrivateKmEdited(d)));

    // Celkem
    const tdTotal = document.createElement("td");
    tdTotal.className = "num-input";
    const mismatch = d.csv_km > 0 && totalDayKm !== d.csv_km;
    tdTotal.innerHTML = `<strong style="color:${mismatch ? "var(--red)" : "inherit"}">${totalDayKm}</strong>`;
    if (mismatch) tdTotal.title = `Rozdíl oproti Škoda CSV (${d.csv_km} km)`;
    tr.appendChild(tdTotal);

    // Reason & Project
    tr.appendChild(makeTextCell(d, "reason"));
    tr.appendChild(makeTextCell(d, "project"));

    // Status & Actions
    const tdStatus = document.createElement("td");
    tdStatus.appendChild(makeStatusActions(d));
    tr.appendChild(tdStatus);

    tbody.appendChild(tr);
  }
}

function makeNumCell(obj, field, onChange) {
  const td = document.createElement("td");
  const inp = document.createElement("input");
  inp.type = "number";
  inp.min = "0";
  inp.className = "num-input";
  inp.value = obj[field] || 0;
  inp.addEventListener("change", () => {
    obj[field] = Math.max(0, Math.round(Number(inp.value || 0)));
    obj.locked = true;
    onChange();
  });
  td.appendChild(inp);
  return td;
}

function makeTextCell(obj, field) {
  const td = document.createElement("td");
  const inp = document.createElement("input");
  inp.type = "text";
  inp.value = obj[field] || "";
  inp.addEventListener("change", () => {
    obj[field] = inp.value;
    obj.locked = true;
    persistMonth();
  });
  td.appendChild(inp);
  return td;
}

function onKmEdited(d) {
  const biz = (d.km_city || 0) + (d.km_other || 0);
  if (d.csv_km > 0) {
    d.km_private = Math.max(0, d.csv_km - biz);
  }
  d.status = d.csv_km > 0 && biz > d.csv_km ? "warning" : biz > 0 ? "business" : d.destination ? "ok" : "empty";
  persistMonth();
}

function onPrivateKmEdited(d) {
  const biz = (d.km_city || 0) + (d.km_other || 0);
  if (biz > 0) {
    d.status = d.csv_km > 0 && biz > d.csv_km ? "warning" : "business";
  } else {
    d.status = d.km_private > 0 ? (d.destination ? "ok" : "needs_input") : "empty";
  }
  persistMonth();
}

function makeStatusActions(d) {
  const wrap = document.createElement("div");
  wrap.className = "row-actions";

  const badge = document.createElement("span");
  if (d.status === "auto") {
    badge.className = "badge badge-green";
    badge.textContent = "🟢 Auto";
  } else if (d.status === "ok") {
    badge.className = "badge badge-green";
    badge.textContent = "🟢 OK";
  } else if (d.status === "business") {
    badge.className = "badge badge-blue";
    badge.textContent = "💼 Služ.";
  } else if (d.status === "review") {
    badge.className = "badge badge-yellow";
    badge.textContent = "🟡 Ověřit";
  } else if (d.status === "needs_input") {
    badge.className = "badge badge-red";
    badge.textContent = "🔴 Doplnit";
  } else if (d.status === "warning") {
    badge.className = "badge badge-yellow";
    badge.textContent = "⚠️ > CSV";
  } else {
    badge.className = "badge badge-neutral";
    badge.textContent = "⚪ Volno";
  }
  wrap.appendChild(badge);

  if (d.status === "review" && d.destination) {
    const okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "btn btn-sm btn-secondary";
    okBtn.textContent = "✓";
    okBtn.title = "Potvrdit navržený cíl";
    okBtn.addEventListener("click", () => {
      d.status = "ok";
      d.locked = true;
      persistMonth();
    });
    wrap.appendChild(okBtn);
  }

  if (d.status === "warning" && d.csv_km > 0) {
    const fitBtn = document.createElement("button");
    fitBtn.type = "button";
    fitBtn.className = "btn btn-sm btn-secondary";
    fitBtn.textContent = "Zarovnat";
    fitBtn.title = `Upravit služební km na nájezd z CSV (${d.csv_km} km)`;
    fitBtn.addEventListener("click", () => {
      if ((d.km_city || 0) >= (d.km_other || 0)) {
        d.km_city = d.csv_km;
        d.km_other = 0;
      } else {
        d.km_other = d.csv_km;
        d.km_city = 0;
      }
      d.km_private = 0;
      d.status = "business";
      d.locked = true;
      persistMonth();
    });
    wrap.appendChild(fitBtn);
  }

  if (d.km_city > 0 || d.km_other > 0) {
    const clrBtn = document.createElement("button");
    clrBtn.type = "button";
    clrBtn.className = "icon-btn";
    clrBtn.textContent = "✕";
    clrBtn.title = "Odebrat služební cestu ze dne";
    clrBtn.addEventListener("click", async () => {
      try {
        const res = await apiJson(`/api/month/${state.activeMonth}/day/${d.day}/clear-business`, {
          method: "POST",
        });
        state.monthData = res.month_data;
        state.summary = res.summary;
        renderAllMonthUI();
      } catch (err) {
        showToast(err.message, "error");
      }
    });
    wrap.appendChild(clrBtn);
  }

  return wrap;
}

async function applySuggestionToDay(d, s) {
  if (s.kind === "business") {
    try {
      const res = await apiJson(`/api/month/${state.activeMonth}/business-trip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          date: d.date,
          destination: s.destination,
          km_city: s.km_city,
          km_other: s.km_other,
          reason: s.reason,
          project: s.project,
          append_commute: s.append_commute,
        }),
      });
      state.monthData = res.month_data;
      state.summary = res.summary;
      renderAllMonthUI();
      showToast(`Zapsána služební cesta na ${d.date}.`, "success");
    } catch (err) {
      showToast(err.message, "error");
    }
    return;
  }

  d.destination = s.destination;
  d.km_city = 0;
  d.km_other = 0;
  d.km_private = d.csv_km || d.km_private || 0;
  d.status = "ok";
  d.locked = true;
  persistMonth();
}

async function persistMonth() {
  if (!state.monthData) return;
  try {
    const res = await apiJson(`/api/month/${state.activeMonth}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.monthData),
    });
    state.monthData = res.month_data;
    state.summary = res.summary;
    renderAllMonthUI();
  } catch (err) {
    showToast(`Chyba při ukládání: ${err.message}`, "error");
  }
}

/* Receipts & Expenses */
function getReceiptStatusBadgeHtml(f) {
  if (state.extractingFiles.has(f.filename)) {
    return '<span class="badge badge-yellow">⏳ Vytěžuji...</span>';
  }
  if (f.status === "extracted_ai") {
    return '<span class="badge badge-green">🟢 Vytěženo AI</span>';
  }
  if (f.status === "manual") {
    return '<span class="badge badge-blue">🔵 Zpracováno ručně</span>';
  }
  if (f.status === "error") {
    return '<span class="badge badge-red">🔴 Chyba — doplnit ručně</span>';
  }
  return '<span class="badge badge-neutral">⚪ Nezpracováno</span>';
}

function summarizeReceiptItems(f) {
  const parts = [];
  if (f.merchant) parts.push(f.merchant);
  for (const fi of f.fuel_items || []) {
    parts.push(`PHM: ${fmtNum(fi.liters)} l × ${fmtNum(fi.unit_price)} ${f.currency || "CZK"}`);
  }
  for (const oi of f.other_items || []) {
    parts.push(`${oi.description || "Ostatní"}: ${fmtNum(oi.amount)} ${f.currency || "CZK"}`);
  }
  if (parts.length > 0) return parts.join(" · ");
  if (f.status === "error" && f.error_message) return f.error_message;
  return "Zatím bez údajů";
}

function renderReceiptFiles() {
  const tbody = document.getElementById("receipt-files-tbody");
  const countEl = document.getElementById("inputs-receipt-count");
  const btnAll = document.getElementById("btn-process-all-inputs");
  const files = state.receiptFiles || [];

  const monthFiles = files.filter((f) => f.assigned_month === state.activeMonth);
  const pendingFiles = files.filter((f) => f.status === "unprocessed" || f.status === "error");

  countEl.textContent = files.length;
  document.getElementById("rcnt-all").textContent = files.length;
  document.getElementById("rcnt-month").textContent = monthFiles.length;
  document.getElementById("rcnt-pending").textContent = pendingFiles.length;
  document.getElementById("unprocessed-count").textContent = pendingFiles.length;

  if (pendingFiles.length > 0) {
    btnAll.classList.remove("hidden");
  } else {
    btnAll.classList.add("hidden");
  }

  const visible = files.filter((f) => {
    if (state.receiptFilter === "month") return f.assigned_month === state.activeMonth;
    if (state.receiptFilter === "pending") return f.status === "unprocessed" || f.status === "error";
    return true;
  });

  tbody.innerHTML = "";
  if (visible.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:16px;">Žádné soubory pro tento filtr.</td></tr>';
    return;
  }

  for (const f of visible) {
    const tr = document.createElement("tr");
    if (f.status === "error") tr.classList.add("row-needs-input");

    const kb = Math.round((f.size_bytes || 0) / 1024);
    const tdName = document.createElement("td");
    tdName.innerHTML = `<strong>📄 ${escapeHtml(f.filename)}</strong> <span class="muted small">(${kb} KB)</span>`;
    tr.appendChild(tdName);

    const tdDate = document.createElement("td");
    const monthBadge = f.assigned_month
      ? `<span class="badge ${f.assigned_month === state.activeMonth ? "badge-blue" : "badge-neutral"}">${escapeHtml(f.assigned_month)}</span>`
      : '<span class="muted">—</span>';
    tdDate.innerHTML = `<div>${escapeHtml(f.receipt_date || "—")}</div><div style="margin-top:2px;">${monthBadge}</div>`;
    tr.appendChild(tdDate);

    const tdStatus = document.createElement("td");
    tdStatus.innerHTML = getReceiptStatusBadgeHtml(f);
    tr.appendChild(tdStatus);

    const tdSummary = document.createElement("td");
    tdSummary.className = f.status === "error" ? "small" : "";
    if (f.status === "error") tdSummary.style.color = "var(--red)";
    tdSummary.textContent = summarizeReceiptItems(f);
    tr.appendChild(tdSummary);

    const tdActions = document.createElement("td");
    tdActions.className = "row-actions";

    const isBusy = state.extractingFiles.has(f.filename);
    const extBtn = document.createElement("button");
    extBtn.type = "button";
    extBtn.className = "btn btn-sm btn-primary";
    extBtn.textContent = isBusy ? "⏳..." : "🤖 Vytěžit";
    extBtn.disabled = isBusy;
    extBtn.addEventListener("click", () => extractSingleReceiptFile(f.filename));

    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "btn btn-sm btn-secondary";
    openBtn.textContent = f.status === "unprocessed" || f.status === "error" ? "👁️ Doplnit ručně" : "👁️ Otevřít / Upravit";
    openBtn.addEventListener("click", () => openReceiptEditor(f));

    tdActions.appendChild(extBtn);
    tdActions.appendChild(openBtn);
    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  }
}

async function extractSingleReceiptFile(filename) {
  state.extractingFiles.add(filename);
  renderReceiptFiles();
  showToast(`Vytěžuji ${filename} přes Gemini AI...`);
  try {
    const res = await apiJson(
      `/api/receipt-files/${encodeURIComponent(filename)}/extract?active_month=${encodeURIComponent(state.activeMonth)}`,
      { method: "POST" }
    );
    state.receiptFiles = res.receipt_files || state.receiptFiles;
    state.monthData = res.month_data;
    state.summary = res.summary;
    const fe = res.file_entry;
    if (fe?.status === "error") {
      showToast(`Nepodařilo se vytěžit ${filename}: ${fe.error_message} — otevřete soubor pro ruční zadání.`, "error");
    } else {
      const mInfo = fe?.assigned_month && fe.assigned_month !== state.activeMonth
        ? ` (přiřazeno do měsíce ${fe.assigned_month})`
        : "";
      showToast(`Účtenka ${filename} vytěžena${mInfo}.`, "success");
    }
  } catch (err) {
    showToast(`Chyba vytěžování ${filename}: ${err.message}`, "error");
  } finally {
    state.extractingFiles.delete(filename);
    renderAllMonthUI();
  }
}

async function processAllPendingReceipts(btnEl) {
  const pending = (state.receiptFiles || []).filter(
    (f) => f.status === "unprocessed" || f.status === "error"
  );
  if (pending.length === 0) return;
  if (btnEl) btnEl.disabled = true;
  try {
    for (const f of pending) {
      await extractSingleReceiptFile(f.filename);
    }
  } finally {
    if (btnEl) btnEl.disabled = false;
  }
}

async function uploadReceiptFiles(fileList) {
  if (!fileList || fileList.length === 0) return;
  const dz = document.getElementById("receipt-dropzone");
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  dz.style.opacity = "0.6";
  showToast(`Nahrávám a vytěžuji (${fileList.length}) přes Google Gemini API...`);
  try {
    const res = await apiJson(`/api/month/${state.activeMonth}/receipts/upload`, {
      method: "POST",
      body: fd,
    });
    state.receiptFiles = res.receipt_files || state.receiptFiles;
    state.monthData = res.month_data;
    state.summary = res.summary;
    renderAllMonthUI();
    if (res.errors?.length) {
      showToast(`Nahráno s chybou vytěžení (doplňte ručně v seznamu): ${res.errors.join(" | ")}`, "error");
    } else {
      showToast(`Účtenky (${res.processed?.length || 0}) nahrány a vytěženy.`, "success");
    }
  } catch (err) {
    showToast(`Chyba nahrávání: ${err.message}`, "error");
  } finally {
    dz.style.opacity = "1";
  }
}

function renderPendingReceipts() {
  const wrap = document.getElementById("pending-receipts-wrap");
  const pending = state.monthData?.pending_receipts || [];
  if (pending.length === 0) {
    wrap.classList.add("hidden");
    wrap.innerHTML = "";
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = "";
  for (const p of pending) {
    const r = p.receipt;
    const card = document.createElement("div");
    card.className = "pending-card";
    const itemsSummary = [
      ...p.fuel_entries.map((f) => `PHM: ${f.liters} l × ${f.unit_price} ${f.currency}`),
      ...p.other_entries.map((o) => `Ostatní: ${o.description} (${o.amount} ${o.currency})`),
    ].join(" · ");

    const info = document.createElement("div");
    info.innerHTML = `
      <strong>📅 Účtenka „${escapeHtml(r.filename)}“ je z jiného měsíce (${escapeHtml(r.receipt_date || "?")})</strong>
      <div class="small muted">${escapeHtml(r.merchant || "")} · ${escapeHtml(itemsSummary)}</div>
    `;

    const btns = document.createElement("div");
    btns.className = "row-actions";

    if (r.detected_month) {
      const moveBtn = document.createElement("button");
      moveBtn.type = "button";
      moveBtn.className = "btn btn-sm btn-primary";
      moveBtn.textContent = `➡️ Přesunout do ${r.detected_month}`;
      moveBtn.addEventListener("click", () => resolvePendingReceipt(r.id, "move", r.detected_month));
      btns.appendChild(moveBtn);
    }

    const keepBtn = document.createElement("button");
    keepBtn.type = "button";
    keepBtn.className = "btn btn-sm btn-secondary";
    keepBtn.textContent = `📌 Ponechat zde (${state.activeMonth})`;
    keepBtn.addEventListener("click", () => resolvePendingReceipt(r.id, "keep"));
    btns.appendChild(keepBtn);

    const discBtn = document.createElement("button");
    discBtn.type = "button";
    discBtn.className = "icon-btn";
    discBtn.textContent = "🗑️";
    discBtn.addEventListener("click", () => resolvePendingReceipt(r.id, "discard"));
    btns.appendChild(discBtn);

    card.appendChild(info);
    card.appendChild(btns);
    wrap.appendChild(card);
  }
}

async function resolvePendingReceipt(receiptId, action, targetMonth = null) {
  try {
    const res = await apiJson(`/api/month/${state.activeMonth}/receipts/resolve-pending`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ receipt_id: receiptId, action, target_month: targetMonth }),
    });
    state.monthData = res.month_data;
    state.summary = res.summary;
    renderAllMonthUI();
    showToast(
      action === "move" ? `Účtenka přesunuta do ${targetMonth}.` : action === "keep" ? "Účtenka ponechána v tomto měsíci." : "Účtenka odstraněna.",
      "success"
    );
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderFuelReceipts() {
  const tbody = document.getElementById("fuel-tbody");
  const rows = state.monthData?.fuel_receipts || [];
  tbody.innerHTML = "";

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted" style="text-align:center;padding:14px;">Zatím žádné účtenky PHM — v Excelu se použije vyhlášková cena.</td></tr>';
    return;
  }

  rows.forEach((fr, idx) => {
    const tr = document.createElement("tr");

    // Date
    const tdDate = document.createElement("td");
    const dInp = document.createElement("input");
    dInp.type = "text";
    dInp.value = fr.date || "";
    dInp.placeholder = "YYYY-MM-DD";
    dInp.addEventListener("change", () => { fr.date = dInp.value; persistMonth(); });
    tdDate.appendChild(dInp);
    tr.appendChild(tdDate);

    // Merchant / Desc
    const tdDesc = document.createElement("td");
    const descInp = document.createElement("input");
    descInp.type = "text";
    descInp.value = fr.merchant ? `${fr.merchant} (${fr.description || "PHM"})` : (fr.description || "");
    descInp.addEventListener("change", () => {
      fr.description = descInp.value;
      fr.merchant = "";
      persistMonth();
    });
    tdDesc.appendChild(descInp);
    tr.appendChild(tdDesc);

    // Liters
    const tdLit = document.createElement("td");
    const litInp = document.createElement("input");
    litInp.type = "number";
    litInp.step = "0.01";
    litInp.className = "num-input";
    litInp.value = fr.liters ?? 0;
    litInp.addEventListener("change", () => {
      fr.liters = Number(litInp.value || 0);
      fr.total_czk = +(fr.liters * (fr.unit_price || 0) * (fr.exchange_rate || 1)).toFixed(2);
      persistMonth();
    });
    tdLit.appendChild(litInp);
    tr.appendChild(tdLit);

    // Currency
    const tdCurr = document.createElement("td");
    const curSel = document.createElement("select");
    for (const c of ["CZK", "EUR", "PLN", "HUF"]) {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      curSel.appendChild(opt);
    }
    curSel.value = fr.currency || "CZK";
    curSel.addEventListener("change", async () => {
      fr.currency = curSel.value;
      const rateRes = await apiJson(`/api/cnb-rate?currency=${fr.currency}&date=${encodeURIComponent(fr.date || "")}`);
      fr.exchange_rate = rateRes.rate;
      fr.total_czk = +(fr.liters * (fr.unit_price || 0) * fr.exchange_rate).toFixed(2);
      persistMonth();
    });
    tdCurr.appendChild(curSel);
    tr.appendChild(tdCurr);

    // Unit price
    const tdPrice = document.createElement("td");
    const pInp = document.createElement("input");
    pInp.type = "number";
    pInp.step = "0.01";
    pInp.className = "num-input";
    pInp.value = fr.unit_price ?? 0;
    pInp.addEventListener("change", () => {
      fr.unit_price = Number(pInp.value || 0);
      fr.total_czk = +((fr.liters || 0) * fr.unit_price * (fr.exchange_rate || 1)).toFixed(2);
      persistMonth();
    });
    tdPrice.appendChild(pInp);
    tr.appendChild(tdPrice);

    // Exchange rate
    const tdRate = document.createElement("td");
    const rInp = document.createElement("input");
    rInp.type = "number";
    rInp.step = "0.001";
    rInp.className = "num-input";
    rInp.value = fr.exchange_rate ?? 1;
    rInp.disabled = (fr.currency || "CZK") === "CZK";
    rInp.addEventListener("change", () => {
      fr.exchange_rate = Number(rInp.value || 1);
      fr.total_czk = +((fr.liters || 0) * (fr.unit_price || 0) * fr.exchange_rate).toFixed(2);
      persistMonth();
    });
    tdRate.appendChild(rInp);
    tr.appendChild(tdRate);

    // Total CZK
    const tdTot = document.createElement("td");
    tdTot.className = "num-input";
    const total = (fr.liters || 0) * (fr.unit_price || 0) * (fr.exchange_rate || 1);
    tdTot.innerHTML = `<strong>${fmtNum(total)}</strong>`;
    tr.appendChild(tdTot);

    // Actions
    const tdAct = document.createElement("td");
    tdAct.className = "row-actions";
    const refFile = fr.source_filename || fr.filename || fr.stored_file;
    if (refFile) {
      const prevBtn = document.createElement("button");
      prevBtn.type = "button";
      prevBtn.className = "icon-btn";
      prevBtn.textContent = "👁️";
      prevBtn.title = refFile;
      prevBtn.addEventListener("click", () => openReceiptByFilename(refFile));
      tdAct.appendChild(prevBtn);
    }
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "icon-btn";
    delBtn.textContent = "🗑️";
    delBtn.addEventListener("click", () => {
      state.monthData.fuel_receipts.splice(idx, 1);
      persistMonth();
    });
    tdAct.appendChild(delBtn);
    tr.appendChild(tdAct);

    tbody.appendChild(tr);
  });
}

function renderOtherExpenses() {
  const tbody = document.getElementById("other-tbody");
  const rows = state.monthData?.other_expenses || [];
  tbody.innerHTML = "";

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center;padding:14px;">Žádné ostatní výdaje (myčka, ostřikovače, AdBlue).</td></tr>';
    return;
  }

  rows.forEach((oe, idx) => {
    const tr = document.createElement("tr");

    const tdDate = document.createElement("td");
    const dInp = document.createElement("input");
    dInp.type = "text";
    dInp.value = oe.date || "";
    dInp.placeholder = "YYYY-MM-DD";
    dInp.addEventListener("change", () => { oe.date = dInp.value; persistMonth(); });
    tdDate.appendChild(dInp);
    tr.appendChild(tdDate);

    const tdDesc = document.createElement("td");
    const descInp = document.createElement("input");
    descInp.type = "text";
    descInp.value = oe.description || "";
    descInp.addEventListener("change", () => { oe.description = descInp.value; persistMonth(); });
    tdDesc.appendChild(descInp);
    tr.appendChild(tdDesc);

    const tdAmt = document.createElement("td");
    const amtInp = document.createElement("input");
    amtInp.type = "number";
    amtInp.step = "0.01";
    amtInp.className = "num-input";
    amtInp.value = oe.amount ?? 0;
    amtInp.addEventListener("change", () => { oe.amount = Number(amtInp.value || 0); persistMonth(); });
    tdAmt.appendChild(amtInp);
    tr.appendChild(tdAmt);

    const tdCurr = document.createElement("td");
    const curSel = document.createElement("select");
    for (const c of ["CZK", "EUR", "PLN", "HUF"]) {
      const opt = document.createElement("option");
      opt.value = c;
      opt.textContent = c;
      curSel.appendChild(opt);
    }
    curSel.value = oe.currency || "CZK";
    curSel.addEventListener("change", async () => {
      oe.currency = curSel.value;
      const rateRes = await apiJson(`/api/cnb-rate?currency=${oe.currency}&date=${encodeURIComponent(oe.date || "")}`);
      oe.exchange_rate = rateRes.rate;
      persistMonth();
    });
    tdCurr.appendChild(curSel);
    tr.appendChild(tdCurr);

    const tdRate = document.createElement("td");
    const rInp = document.createElement("input");
    rInp.type = "number";
    rInp.step = "0.001";
    rInp.className = "num-input";
    rInp.value = oe.exchange_rate ?? 1;
    rInp.disabled = (oe.currency || "CZK") === "CZK";
    rInp.addEventListener("change", () => { oe.exchange_rate = Number(rInp.value || 1); persistMonth(); });
    tdRate.appendChild(rInp);
    tr.appendChild(tdRate);

    const tdProj = document.createElement("td");
    const pInp = document.createElement("input");
    pInp.type = "text";
    pInp.value = oe.project || "";
    pInp.placeholder = "volitelné";
    pInp.addEventListener("change", () => { oe.project = pInp.value; persistMonth(); });
    tdProj.appendChild(pInp);
    tr.appendChild(tdProj);

    const tdAct = document.createElement("td");
    tdAct.className = "row-actions";
    const refFile = oe.source_filename || oe.filename || oe.stored_file;
    if (refFile) {
      const prevBtn = document.createElement("button");
      prevBtn.type = "button";
      prevBtn.className = "icon-btn";
      prevBtn.textContent = "👁️";
      prevBtn.title = refFile;
      prevBtn.addEventListener("click", () => openReceiptByFilename(refFile));
      tdAct.appendChild(prevBtn);
    }
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "icon-btn";
    delBtn.textContent = "🗑️";
    delBtn.addEventListener("click", () => {
      state.monthData.other_expenses.splice(idx, 1);
      persistMonth();
    });
    tdAct.appendChild(delBtn);
    tr.appendChild(tdAct);

    tbody.appendChild(tr);
  });
}

function renderPSASummary() {
  const s = state.summary;
  const p = state.config?.profile || {};
  if (!s) return;

  document.getElementById("psa-fuel-price").textContent = `${fmtNum(s.effective_fuel_price)} Kč/l`;
  document.getElementById("psa-fuel-price-source").textContent =
    s.avg_fuel_price > 0
      ? `Průměr z ${fmtNum(s.total_liters_refueled)} l účtenek`
      : `Vyhlášková cena (${fmtNum(s.statutory_fuel_price)} Kč/l)`;

  document.getElementById("psa-consumption").textContent = `${p.consumption_city} / ${p.consumption_combined} l/100 km`;
  document.getElementById("psa-est-liters").textContent = `${fmtNum(s.estimated_liters_consumed)} l`;

  const fuelEl = document.getElementById("psa-fuel-amount");
  fuelEl.textContent = `${fmtNum(s.psa_fuel_czk)} Kč`;
  fuelEl.dataset.copyVal = s.psa_fuel_czk.toFixed(2);

  const otherEl = document.getElementById("psa-other-amount");
  otherEl.textContent = `${fmtNum(s.psa_other_czk)} Kč`;
  otherEl.dataset.copyVal = s.psa_other_czk.toFixed(2);

  document.getElementById("psa-biz-share").textContent = `${fmtNum(s.business_share_pct, 1)} %`;
}

/* Two-Column Receipt Viewer & Manual Editor */
function openReceiptByFilename(fname) {
  const entry = (state.receiptFiles || []).find((x) => x.filename === fname);
  if (entry) {
    openReceiptEditor(entry);
    return;
  }
  openReceiptEditor({
    filename: fname,
    mime_type: fname.toLowerCase().endsWith(".pdf") ? "application/pdf" : "image/jpeg",
    status: "unprocessed",
    receipt_date: "",
    assigned_month: state.activeMonth,
    merchant: "",
    currency: "CZK",
    exchange_rate: 1.0,
    fuel_items: [],
    other_items: [],
    error_message: "",
  });
}

function openReceiptEditor(entry) {
  const year = state.config?.profile?.year || 2026;
  const fuelClone = (entry.fuel_items || []).map((x) => ({ ...x }));
  const otherClone = (entry.other_items || []).map((x) => ({ ...x }));
  if (fuelClone.length === 0 && otherClone.length === 0) {
    fuelClone.push({ id: `f-${Date.now()}`, description: "Nafta", liters: 0, unit_price: 0 });
  }

  state.editorDraft = {
    filename: entry.filename,
    mime_type: entry.mime_type || "",
    status: entry.status || "unprocessed",
    assigned_month: entry.assigned_month || state.activeMonth,
    receipt_date: entry.receipt_date || "",
    merchant: entry.merchant || "",
    currency: entry.currency || "CZK",
    exchange_rate: entry.exchange_rate ?? 1.0,
    fuel_items: fuelClone,
    other_items: otherClone,
    error_message: entry.error_message || "",
  };

  const fileUrl = `/api/inputs/file/${encodeURIComponent(entry.filename)}`;
  document.getElementById("rec-ed-title").textContent = entry.filename;
  document.getElementById("rec-ed-status-badge").outerHTML = getReceiptStatusBadgeHtml(entry).replace(
    'class="badge',
    'id="rec-ed-status-badge" class="badge'
  );
  document.getElementById("rec-ed-open-tab").href = fileUrl;

  const errBox = document.getElementById("rec-ed-error-box");
  if (entry.error_message) {
    errBox.textContent = `Poznámka z AI vytěžení: ${entry.error_message}`;
    errBox.classList.remove("hidden");
  } else {
    errBox.classList.add("hidden");
  }

  // Left viewer pane
  const viewer = document.getElementById("rec-ed-viewer");
  viewer.innerHTML = "";
  const isPdf = entry.mime_type === "application/pdf" || entry.filename.toLowerCase().endsWith(".pdf");
  if (isPdf) {
    const iframe = document.createElement("iframe");
    iframe.src = fileUrl;
    viewer.appendChild(iframe);
  } else {
    const img = document.createElement("img");
    img.src = fileUrl;
    viewer.appendChild(img);
  }

  // Month select
  const monthSel = document.getElementById("rec-ed-month");
  monthSel.innerHTML = "";
  for (let m = 1; m <= 12; m++) {
    const mk = `${year}-${String(m).padStart(2, "0")}`;
    const opt = document.createElement("option");
    opt.value = mk;
    opt.textContent = `${String(m).padStart(2, "0")}/${year} ${MONTH_NAMES_CS[m - 1]}`;
    monthSel.appendChild(opt);
  }
  monthSel.value = state.editorDraft.assigned_month;

  document.getElementById("rec-ed-date").value = state.editorDraft.receipt_date;
  document.getElementById("rec-ed-merchant").value = state.editorDraft.merchant;
  document.getElementById("rec-ed-currency").value = state.editorDraft.currency;
  document.getElementById("rec-ed-rate").value = state.editorDraft.exchange_rate;

  renderEditorItems();
  document.getElementById("receipt-editor-modal").classList.remove("hidden");
}

function renderEditorItems() {
  const draft = state.editorDraft;
  if (!draft) return;

  const fuelWrap = document.getElementById("rec-ed-fuel-list");
  fuelWrap.innerHTML = "";
  draft.fuel_items.forEach((fi, idx) => {
    const row = document.createElement("div");
    row.className = "editor-item-row";
    const totalVal = +((Number(fi.liters || 0) * Number(fi.unit_price || 0)).toFixed(2));
    row.innerHTML = `
      <div class="field"><label>Popis paliva</label><input type="text" data-k="description" value="${escapeAttr(fi.description || "Nafta")}" /></div>
      <div class="field"><label>Litrů</label><input type="number" step="0.01" class="num-input" data-k="liters" value="${fi.liters || 0}" /></div>
      <div class="field"><label>Cena / 1 l</label><input type="number" step="0.01" class="num-input" data-k="unit_price" value="${fi.unit_price || 0}" /></div>
      <div class="field"><label>Celkem</label><input type="number" step="0.01" class="num-input" data-k="total" value="${totalVal}" /></div>
      <button type="button" class="icon-btn" title="Smazat položku">🗑️</button>
    `;
    const descInp = row.querySelector('[data-k="description"]');
    const litInp = row.querySelector('[data-k="liters"]');
    const priceInp = row.querySelector('[data-k="unit_price"]');
    const totInp = row.querySelector('[data-k="total"]');

    descInp.addEventListener("change", () => { fi.description = descInp.value; });
    litInp.addEventListener("input", () => {
      fi.liters = Number(litInp.value || 0);
      if (fi.unit_price > 0) {
        totInp.value = (fi.liters * fi.unit_price).toFixed(2);
      } else if (Number(totInp.value || 0) > 0 && fi.liters > 0) {
        fi.unit_price = +(Number(totInp.value) / fi.liters).toFixed(2);
        priceInp.value = fi.unit_price;
      }
    });
    priceInp.addEventListener("input", () => {
      fi.unit_price = Number(priceInp.value || 0);
      totInp.value = (Number(fi.liters || 0) * fi.unit_price).toFixed(2);
    });
    totInp.addEventListener("input", () => {
      const tot = Number(totInp.value || 0);
      if (Number(fi.liters || 0) > 0) {
        fi.unit_price = +(tot / fi.liters).toFixed(2);
        priceInp.value = fi.unit_price;
      }
    });
    row.querySelector("button").addEventListener("click", () => {
      draft.fuel_items.splice(idx, 1);
      renderEditorItems();
    });
    fuelWrap.appendChild(row);
  });

  const otherWrap = document.getElementById("rec-ed-other-list");
  otherWrap.innerHTML = "";
  if (draft.other_items.length === 0) {
    otherWrap.innerHTML = '<div class="muted small">Žádné ostatní výdaje (přidejte tlačítkem + Přidat výdaj).</div>';
  }
  draft.other_items.forEach((oi, idx) => {
    const row = document.createElement("div");
    row.className = "editor-item-row other-row";
    row.innerHTML = `
      <div class="field"><label>Popis výdaje</label><input type="text" data-k="description" value="${escapeAttr(oi.description || "")}" placeholder="např. Mytí vozu, AdBlue, Ostřikovače" /></div>
      <div class="field"><label>Částka</label><input type="number" step="0.01" class="num-input" data-k="amount" value="${oi.amount || 0}" /></div>
      <div class="field"><label>Projekt (volit.)</label><input type="text" data-k="project" value="${escapeAttr(oi.project || "")}" /></div>
      <button type="button" class="icon-btn" title="Smazat položku">🗑️</button>
    `;
    const descInp = row.querySelector('[data-k="description"]');
    const amtInp = row.querySelector('[data-k="amount"]');
    const projInp = row.querySelector('[data-k="project"]');
    descInp.addEventListener("change", () => { oi.description = descInp.value; });
    amtInp.addEventListener("input", () => { oi.amount = Number(amtInp.value || 0); });
    projInp.addEventListener("change", () => { oi.project = projInp.value; });
    row.querySelector("button").addEventListener("click", () => {
      draft.other_items.splice(idx, 1);
      renderEditorItems();
    });
    otherWrap.appendChild(row);
  });
}

async function saveManualReceiptEditor() {
  const draft = state.editorDraft;
  if (!draft) return;

  const payload = {
    assigned_month: document.getElementById("rec-ed-month").value,
    receipt_date: document.getElementById("rec-ed-date").value.trim(),
    merchant: document.getElementById("rec-ed-merchant").value.trim(),
    currency: document.getElementById("rec-ed-currency").value,
    exchange_rate: Number(document.getElementById("rec-ed-rate").value || 1),
    fuel_items: draft.fuel_items.filter((x) => Number(x.liters || 0) > 0 || Number(x.unit_price || 0) > 0),
    other_items: draft.other_items.filter((x) => Number(x.amount || 0) > 0),
  };

  try {
    const res = await apiJson(
      `/api/receipt-files/${encodeURIComponent(draft.filename)}/manual?active_month=${encodeURIComponent(state.activeMonth)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    );
    state.receiptFiles = res.receipt_files || state.receiptFiles;
    state.monthData = res.month_data;
    state.summary = res.summary;
    document.getElementById("receipt-editor-modal").classList.add("hidden");
    renderAllMonthUI();
    showToast(`Účtenka ${draft.filename} uložena do ${payload.assigned_month} (Zpracováno ručně).`, "success");
  } catch (err) {
    showToast(`Chyba při ukládání účtenky: ${err.message}`, "error");
  }
}

function openSettingsModal() {
  const p = state.config.profile;
  document.getElementById("cfg-license-plate").value = p.license_plate || "";
  document.getElementById("cfg-vehicle-type").value = p.vehicle_type || "Manažerský ";
  document.getElementById("cfg-fuel-type").value = p.fuel_type || "nafta";
  document.getElementById("cfg-cons-city").value = p.consumption_city ?? 5.0;
  document.getElementById("cfg-cons-comb").value = p.consumption_combined ?? 5.0;
  document.getElementById("cfg-init-odo").value = p.initial_odometer ?? 7;
  document.getElementById("cfg-driver-name").value = p.driver_name || "";
  document.getElementById("cfg-staff-no").value = p.staff_number || "";
  document.getElementById("cfg-department").value = p.department || "";
  document.getElementById("cfg-template-path").value = p.template_path || "";
  document.getElementById("cfg-output-path").value = p.output_path || "";
  document.getElementById("cfg-gemini-key").value = p.gemini_api_key || "";
  document.getElementById("gemini-test-result").textContent = state.geminiKeyConfigured
    ? "API klíč je nakonfigurován."
    : "API klíč zatím není nastaven.";

  renderCatalogLists();
  document.getElementById("settings-modal").classList.remove("hidden");
}

function renderCatalogLists() {
  // Business routes
  const bizWrap = document.getElementById("biz-catalog-list");
  bizWrap.innerHTML = "";
  state.config.business_routes.forEach((r, idx) => {
    const div = document.createElement("div");
    div.className = "catalog-item";
    div.innerHTML = `
      <div class="field"><label>Název</label><input type="text" value="${escapeAttr(r.name)}" data-k="name" /></div>
      <div class="field"><label>Cíl cesty (B)</label><input type="text" value="${escapeAttr(r.destination)}" data-k="destination" /></div>
      <div class="field"><label>Město km</label><input type="number" value="${r.km_city || 0}" data-k="km_city" /></div>
      <div class="field"><label>Ostatní km</label><input type="number" value="${r.km_other || 0}" data-k="km_other" /></div>
      <div class="field"><label>Důvod (G)</label><input type="text" value="${escapeAttr(r.reason)}" data-k="reason" /></div>
      <div class="field"><label>Projekt (H)</label><input type="text" value="${escapeAttr(r.project)}" data-k="project" /></div>
      <div class="field field-check"><label class="checkbox-inline"><input type="checkbox" ${r.append_commute ? "checked" : ""} data-k="append_commute" /> + soukr. Středokluky</label></div>
      <div class="field"><button type="button" class="btn btn-sm btn-secondary" data-del-biz="${idx}">🗑️ Smazat</button></div>
    `;
    div.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("change", () => {
        const k = inp.dataset.k;
        if (inp.type === "checkbox") r[k] = inp.checked;
        else if (inp.type === "number") r[k] = Number(inp.value || 0);
        else r[k] = inp.value;
      });
    });
    div.querySelector(`[data-del-biz="${idx}"]`).addEventListener("click", () => {
      state.config.business_routes.splice(idx, 1);
      renderCatalogLists();
    });
    bizWrap.appendChild(div);
  });

  // Private routes
  const privWrap = document.getElementById("priv-catalog-list");
  privWrap.innerHTML = "";
  state.config.private_routes.forEach((r, idx) => {
    const div = document.createElement("div");
    div.className = "catalog-item";
    div.innerHTML = `
      <div class="field"><label>Název</label><input type="text" value="${escapeAttr(r.name)}" data-k="name" /></div>
      <div class="field"><label>Text do sl. B</label><input type="text" value="${escapeAttr(r.destination)}" data-k="destination" /></div>
      <div class="field"><label>Min km</label><input type="number" value="${r.min_km || 0}" data-k="min_km" /></div>
      <div class="field"><label>Max km</label><input type="number" value="${r.max_km || 0}" data-k="max_km" /></div>
      <div class="field"><label>Typických km</label><input type="number" value="${r.typical_km || 0}" data-k="typical_km" /></div>
      <div class="field field-check"><label class="checkbox-inline"><input type="checkbox" ${r.is_default_commute ? "checked" : ""} data-k="is_default_commute" /> Denní dojíždění</label></div>
      <div class="field"><button type="button" class="btn btn-sm btn-secondary" data-del-priv="${idx}">🗑️ Smazat</button></div>
    `;
    div.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("change", () => {
        const k = inp.dataset.k;
        if (inp.type === "checkbox") r[k] = inp.checked;
        else if (inp.type === "number") r[k] = Number(inp.value || 0);
        else r[k] = inp.value;
      });
    });
    div.querySelector(`[data-del-priv="${idx}"]`).addEventListener("click", () => {
      state.config.private_routes.splice(idx, 1);
      renderCatalogLists();
    });
    privWrap.appendChild(div);
  });
}

function escapeHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(val) {
  return escapeHtml(val);
}

async function saveSettings() {
  const p = state.config.profile;
  p.license_plate = document.getElementById("cfg-license-plate").value.trim();
  p.vehicle_type = document.getElementById("cfg-vehicle-type").value;
  p.fuel_type = document.getElementById("cfg-fuel-type").value;
  p.consumption_city = Number(document.getElementById("cfg-cons-city").value || 5.0);
  p.consumption_combined = Number(document.getElementById("cfg-cons-comb").value || 5.0);
  p.initial_odometer = Number(document.getElementById("cfg-init-odo").value || 0);
  p.driver_name = document.getElementById("cfg-driver-name").value.trim();
  p.staff_number = document.getElementById("cfg-staff-no").value.trim();
  p.department = document.getElementById("cfg-department").value.trim();
  p.template_path = document.getElementById("cfg-template-path").value.trim();
  p.output_path = document.getElementById("cfg-output-path").value.trim();
  p.gemini_api_key = document.getElementById("cfg-gemini-key").value.trim();

  try {
    const res = await apiJson("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.config),
    });
    state.config = res.config;
    state.geminiKeyConfigured = res.gemini_key_configured;
    renderVehicleHeader();
    renderBusinessTemplates();
    renderGeminiWarning();
    await loadMonth(state.activeMonth);
    document.getElementById("settings-modal").classList.add("hidden");
    showToast("Nastavení a katalogy uloženy.", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function exportExcel() {
  try {
    showToast("Generuji vyplněný Excel...");
    const res = await apiJson(`/api/export-excel/${state.activeMonth}`, { method: "POST" });
    showToast(`Excel uložen do ${res.filename}. Spouštím stažení...`, "success");
    window.location.href = res.download_url;
  } catch (err) {
    showToast(err.message, "error");
  }
}

/* Wire up DOM listeners */
document.addEventListener("DOMContentLoaded", () => {
  initApp();

  // Business mode toggle
  document.querySelectorAll("#biz-mode-switch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#biz-mode-switch .seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.bizMode = btn.dataset.mode;
      const isRoute = state.bizMode === "route";
      document.getElementById("biz-catalog-wrap").classList.toggle("hidden", isRoute);
      document.getElementById("biz-route-wrap").classList.toggle("hidden", !isRoute);
      document.getElementById("save-route-catalog-wrap").classList.toggle("hidden", !isRoute);
    });
  });

  // Business template select -> fill inputs
  document.getElementById("biz-template").addEventListener("change", (e) => {
    const id = e.target.value;
    const r = (state.config?.business_routes || []).find((x) => x.id === id);
    if (!r) return;
    document.getElementById("biz-dest-text").value = r.destination || "";
    document.getElementById("biz-km-city").value = r.km_city || 0;
    document.getElementById("biz-km-other").value = r.km_other || 0;
    document.getElementById("biz-reason").value = r.reason || "";
    document.getElementById("biz-project").value = r.project || "";
    document.getElementById("biz-append-commute").checked = Boolean(r.append_commute);
  });

  // Calculate route distance
  document.getElementById("btn-calc-route").addEventListener("click", async () => {
    const origin = document.getElementById("route-origin").value.trim();
    const destination = document.getElementById("route-dest").value.trim();
    const wpRaw = document.getElementById("route-waypoints").value.trim();
    const waypoints = wpRaw ? wpRaw.split(",").map((s) => s.trim()).filter(Boolean) : [];
    const round_trip = document.getElementById("route-roundtrip").checked;

    const btn = document.getElementById("btn-calc-route");
    btn.disabled = true;
    try {
      const res = await apiJson("/api/route-distance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ origin, destination, waypoints, round_trip }),
      });
      document.getElementById("biz-dest-text").value = res.suggested_destination_text;
      document.getElementById("biz-km-city").value = res.km_city;
      document.getElementById("biz-km-other").value = res.km_other;
      const note = document.getElementById("route-result-note");
      note.textContent = `Vypočteno: ${res.distance_km} km (${res.duration_min} min) → zařazeno jako ${res.category === "city" ? "Město (Praha)" : "Ostatní mimo Prahu"}.`;
      note.classList.remove("hidden");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  // Add business trip to selected date
  document.getElementById("btn-add-biz").addEventListener("click", async () => {
    const date = document.getElementById("biz-date").value;
    const destination = document.getElementById("biz-dest-text").value.trim();
    const km_city = Number(document.getElementById("biz-km-city").value || 0);
    const km_other = Number(document.getElementById("biz-km-other").value || 0);
    const reason = document.getElementById("biz-reason").value.trim();
    const project = document.getElementById("biz-project").value;
    const append_commute = document.getElementById("biz-append-commute").checked;

    if (!destination || (km_city + km_other) <= 0) {
      showToast("Vyplňte cíl cesty a počet služebních kilometrů.", "error");
      return;
    }

    try {
      if (state.bizMode === "route" && document.getElementById("biz-save-to-catalog").checked) {
        state.config.business_routes.push({
          id: `custom-${Date.now()}`,
          name: `${destination} (${reason || "služebně"})`,
          destination,
          km_city,
          km_other,
          reason,
          project,
          append_commute,
        });
        await apiJson("/api/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(state.config),
        });
        renderBusinessTemplates();
      }

      const res = await apiJson(`/api/month/${state.activeMonth}/business-trip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, destination, km_city, km_other, reason, project, append_commute }),
      });
      state.monthData = res.month_data;
      state.summary = res.summary;
      renderAllMonthUI();
      showToast(`Služební cesta zapsána ke dni ${date}.`, "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  // Day filters
  document.querySelectorAll("#day-filter-switch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#day-filter-switch .seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.dayFilter = btn.dataset.filter;
      renderDaysTable();
    });
  });

  // Re-estimate unlocked days
  document.getElementById("btn-reestimate").addEventListener("click", async () => {
    try {
      const res = await apiJson(`/api/month/${state.activeMonth}/reestimate`, { method: "POST" });
      state.monthData = res.month_data;
      state.summary = res.summary;
      renderAllMonthUI();
      showToast("Odhady neuzamčených jízd byly aktualizovány podle Škoda CSV.", "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  // CSV upload
  document.getElementById("csv-upload-input").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await apiJson(`/api/upload-csv?active_month=${state.activeMonth}`, {
        method: "POST",
        body: fd,
      });
      state.csvMonths = res.csv_months;
      state.monthData = res.month_data;
      state.summary = res.summary;
      renderMonthBar();
      renderAllMonthUI();
      showToast(`Škoda CSV (${file.name}) nahráno a spárováno.`, "success");
    } catch (err) {
      showToast(err.message, "error");
    } finally {
      e.target.value = "";
    }
  });

  // Receipt dropzone
  const dz = document.getElementById("receipt-dropzone");
  const rInput = document.getElementById("receipt-file-input");
  dz.addEventListener("click", () => rInput.click());
  dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("dragover");
  });
  dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dragover");
    uploadReceiptFiles(e.dataTransfer.files);
  });
  rInput.addEventListener("change", (e) => {
    uploadReceiptFiles(e.target.files);
    e.target.value = "";
  });

  document.getElementById("btn-process-all-inputs").addEventListener("click", (e) => {
    processAllPendingReceipts(e.currentTarget);
  });

  // Receipt filter tabs
  document.querySelectorAll("#receipt-filter-switch .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#receipt-filter-switch .seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.receiptFilter = btn.dataset.rfilter;
      renderReceiptFiles();
    });
  });

  // Two-column Receipt Editor Modal actions
  document.getElementById("rec-ed-date").addEventListener("change", (e) => {
    const val = e.target.value.trim();
    if (/^\d{4}-\d{2}/.test(val)) {
      const mSel = document.getElementById("rec-ed-month");
      const mk = val.slice(0, 7);
      if ([...mSel.options].some((o) => o.value === mk)) mSel.value = mk;
    }
  });

  document.getElementById("rec-ed-currency").addEventListener("change", async (e) => {
    const curr = e.target.value;
    const dVal = document.getElementById("rec-ed-date").value.trim();
    const rateRes = await apiJson(`/api/cnb-rate?currency=${curr}&date=${encodeURIComponent(dVal)}`);
    document.getElementById("rec-ed-rate").value = rateRes.rate;
  });

  document.getElementById("rec-ed-fetch-rate").addEventListener("click", async () => {
    const curr = document.getElementById("rec-ed-currency").value;
    const dVal = document.getElementById("rec-ed-date").value.trim();
    const rateRes = await apiJson(`/api/cnb-rate?currency=${curr}&date=${encodeURIComponent(dVal)}`);
    document.getElementById("rec-ed-rate").value = rateRes.rate;
    showToast(`Kurz ČNB pro ${curr}: ${rateRes.rate}`);
  });

  document.getElementById("rec-ed-add-fuel").addEventListener("click", () => {
    if (!state.editorDraft) return;
    state.editorDraft.fuel_items.push({ id: `f-${Date.now()}`, description: "Nafta", liters: 0, unit_price: 0 });
    renderEditorItems();
  });

  document.getElementById("rec-ed-add-other").addEventListener("click", () => {
    if (!state.editorDraft) return;
    state.editorDraft.other_items.push({ id: `o-${Date.now()}`, description: "Mytí vozu", amount: 0, project: "" });
    renderEditorItems();
  });

  document.getElementById("rec-ed-save-manual").addEventListener("click", saveManualReceiptEditor);

  document.getElementById("rec-ed-retry-ai").addEventListener("click", async () => {
    if (!state.editorDraft) return;
    const fname = state.editorDraft.filename;
    await extractSingleReceiptFile(fname);
    const updated = (state.receiptFiles || []).find((x) => x.filename === fname);
    if (updated) openReceiptEditor(updated);
  });

  // Manual receipt rows
  document.getElementById("btn-add-fuel-manual").addEventListener("click", () => {
    state.monthData.fuel_receipts.push({
      id: `manual-${Date.now()}`,
      date: `${state.activeMonth}-01`,
      merchant: "",
      description: "Nafta",
      liters: 0,
      currency: "CZK",
      unit_price: 0,
      exchange_rate: 1.0,
      total_czk: 0,
    });
    persistMonth();
  });

  document.getElementById("btn-add-other-manual").addEventListener("click", () => {
    state.monthData.other_expenses.push({
      id: `manual-${Date.now()}`,
      date: `${state.activeMonth}-01`,
      merchant: "",
      description: "Mytí vozu",
      amount: 0,
      currency: "CZK",
      exchange_rate: 1.0,
      project: "",
    });
    persistMonth();
  });

  // Export Excel buttons
  document.getElementById("btn-export-excel").addEventListener("click", exportExcel);
  document.getElementById("btn-export-excel-bottom").addEventListener("click", exportExcel);

  // Copy PSA amounts
  ["psa-fuel-amount", "psa-other-amount"].forEach((id) => {
    document.getElementById(id).addEventListener("click", (e) => {
      const val = e.currentTarget.dataset.copyVal;
      if (!val) return;
      navigator.clipboard?.writeText(val);
      showToast(`Zkopírováno do schránky: ${val}`, "success");
    });
  });

  // Settings modal & tabs
  document.getElementById("btn-open-settings").addEventListener("click", openSettingsModal);
  document.getElementById("btn-open-settings-from-banner").addEventListener("click", openSettingsModal);
  document.getElementById("btn-save-settings").addEventListener("click", saveSettings);

  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", () => {
      document.getElementById(el.dataset.closeModal).classList.add("hidden");
    });
  });

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(btn.dataset.tab).classList.add("active");
    });
  });

  document.getElementById("btn-toggle-key").addEventListener("click", () => {
    const inp = document.getElementById("cfg-gemini-key");
    inp.type = inp.type === "password" ? "text" : "password";
  });

  document.getElementById("btn-test-gemini").addEventListener("click", async () => {
    const key = document.getElementById("cfg-gemini-key").value.trim();
    const resEl = document.getElementById("gemini-test-result");
    resEl.textContent = "Testuji spojení...";
    try {
      const res = await apiJson("/api/config/test-gemini", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      });
      resEl.textContent = res.message;
      resEl.style.color = res.ok ? "var(--green)" : "var(--red)";
    } catch (err) {
      resEl.textContent = err.message;
      resEl.style.color = "var(--red)";
    }
  });

  document.getElementById("btn-add-biz-catalog").addEventListener("click", () => {
    state.config.business_routes.push({
      id: `biz-${Date.now()}`,
      name: "Nová služební cesta",
      destination: "",
      km_city: 0,
      km_other: 0,
      reason: "",
      project: "",
      append_commute: false,
    });
    renderCatalogLists();
  });

  document.getElementById("btn-add-priv-catalog").addEventListener("click", () => {
    state.config.private_routes.push({
      id: `priv-${Date.now()}`,
      name: "Nová soukromá cesta",
      destination: "soukr. ",
      min_km: 50,
      max_km: 150,
      typical_km: 100,
      is_default_commute: false,
    });
    renderCatalogLists();
  });
});
