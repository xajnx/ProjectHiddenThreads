const state = {
  q: "",
  kind: "",
  asset_type: "",
  status: "",
  source_domain: "",
  evidence_tier: "",
  discovered_from: "",
  discovered_to: "",
  page: 1,
  page_size: 50,
};

function $(id) {
  return document.getElementById(id);
}

function optionMarkup(value, label) {
  return `<option value="${value}">${label}</option>`;
}

async function fetchSummary() {
  const response = await fetch("/api/summary");
  return response.json();
}

async function fetchInsights() {
  const response = await fetch("/api/insights");
  return response.json();
}

async function fetchRecords() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state)) {
    if (value !== "") {
      params.set(key, String(value));
    }
  }
  const response = await fetch(`/api/records?${params.toString()}`);
  return response.json();
}

async function fetchEntities(entityType = "") {
  const params = new URLSearchParams({ limit: "100" });
  if (entityType) params.set("entity_type", entityType);
  const response = await fetch(`/api/entities?${params.toString()}`);
  return response.json();
}

async function fetchPreview(recordId) {
  const params = new URLSearchParams({ record_id: recordId });
  const response = await fetch(`/api/preview?${params.toString()}`);
  return response.json();
}

function wireFilters(summary) {
  $("kind").innerHTML = optionMarkup("", "All kinds") + summary.kinds.map((v) => optionMarkup(v, v)).join("");
  $("asset_type").innerHTML = optionMarkup("", "All asset types") + summary.asset_types.map((v) => optionMarkup(v, v)).join("");
  $("status").innerHTML = optionMarkup("", "All statuses") + summary.statuses.map((v) => optionMarkup(v, v)).join("");
  $("source_domain").innerHTML = optionMarkup("", "All domains") + summary.source_domains.map((v) => optionMarkup(v, v)).join("");
  $("evidence_tier").innerHTML = optionMarkup("", "All tiers") + summary.evidence_tiers.map((v) => optionMarkup(v, `Tier ${v}`)).join("");
  $("summary").textContent = `Indexed records: ${summary.total_records}`;
}

function bytesLabel(bytes) {
  if (bytes === null || bytes === undefined) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function renderTable(items, total, page, pageSize) {
  const tbody = $("results-body");
  tbody.innerHTML = "";

  for (const item of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.title || "-"}</td>
      <td>${item.kind || "-"}</td>
      <td>${item.asset_type || "-"}</td>
      <td>${item.source_domain || "-"}</td>
      <td>${item.status || "-"}</td>
      <td>${item.evidence_tier ? `Tier ${item.evidence_tier}` : "-"}</td>
      <td>${bytesLabel(item.size_bytes)}</td>
      <td class="actions"><button data-record-id="${item.record_id}">Preview</button></td>
    `;
    tbody.appendChild(tr);
  }

  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  $("page").textContent = `Page ${page} of ${maxPage} • ${total} results`;
  $("prev").disabled = page <= 1;
  $("next").disabled = page >= maxPage;

  tbody.querySelectorAll("button[data-record-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      await renderPreview(button.getAttribute("data-record-id"));
    });
  });
}

function renderInsights(payload) {
  const parseStatusEl = $("parse-status");
  const topEntitiesEl = $("top-entities");

  const parseStatus = payload.parse_status || {};
  const parseRows = Object.entries(parseStatus)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(
      ([key, value]) =>
        `<div class="insight-row"><span class="insight-key">${escapeHtml(String(key))}</span><span class="insight-value">${escapeHtml(String(value))}</span></div>`,
    );
  parseStatusEl.innerHTML = parseRows.length
    ? parseRows.join("")
    : `<div class="insight-row"><span class="insight-key">No parse status data</span><span class="insight-value">-</span></div>`;

  const topEntities = payload.top_entities || [];
  // Collect unique entity types for filter chips
  const entityTypes = [...new Set(topEntities.map((r) => r.entity_type))];
  const chipHtml = entityTypes.map(
    (t) =>
      `<button class="entity-chip" data-type="${escapeHtml(t)}">${escapeHtml(t)}</button>`,
  ).join("");
  const entityRows = topEntities.map(
    (row) =>
      `<div class="insight-row entity-row" data-type="${escapeHtml(String(row.entity_type || ""))}"><span class="insight-key"><span class="etype-badge">${escapeHtml(String(row.entity_type || "?"))}</span> ${escapeHtml(String(row.normalized_text || ""))}</span><span class="insight-value">${escapeHtml(String(row.total_occurrences || 0))}</span></div>`,
  );
  topEntitiesEl.innerHTML = chipHtml
    ? `<div class="entity-chips">${chipHtml}</div>${entityRows.join("")}`
    : `<div class="insight-row"><span class="insight-key">No extracted entities yet</span><span class="insight-value">-</span></div>`;

  // Wire entity type filter chips
  topEntitiesEl.querySelectorAll(".entity-chip").forEach((chip) => {
    chip.addEventListener("click", async () => {
      const active = chip.classList.toggle("active");
      const type = active ? chip.dataset.type : "";
      // Show/hide rows by type
      topEntitiesEl.querySelectorAll(".entity-row").forEach((row) => {
        row.style.display = !type || row.dataset.type === type ? "" : "none";
      });
      topEntitiesEl.querySelectorAll(".entity-chip").forEach((c) => {
        if (c !== chip) c.classList.remove("active");
      });
      await renderEntityBrowse(type);
    });
  });
}

async function renderEntityBrowse(entityType = "") {
  const panel = $("entity-browse");
  if (!panel) return;
  panel.innerHTML = `<div class="empty">Loading${entityType ? ` ${entityType}` : " all"} entities...</div>`;
  const data = await fetchEntities(entityType);
  const entities = data.entities || [];
  if (!entities.length) {
    panel.innerHTML = `<div class="empty">No entities found.</div>`;
    return;
  }
  const rows = entities.map(
    (e) =>
      `<div class="browse-row"><span class="etype-badge">${escapeHtml(e.entity_type)}</span> <span class="browse-text">${escapeHtml(e.normalized_text)}</span> <span class="browse-count">${e.total}×</span> <span class="browse-assets">${e.asset_ids.length} asset${e.asset_ids.length !== 1 ? "s" : ""}</span></div>`,
  );
  panel.innerHTML = `<h3>${escapeHtml(entityType || "All entities")} (${entities.length})</h3>${rows.join("")}`;
}

async function renderPreview(recordId) {
  const payload = await fetchPreview(recordId);
  const panel = $("preview");

  if (payload.error) {
    panel.innerHTML = `<div class="empty">${payload.error}</div>`;
    return;
  }

  const record = payload.record || {};
  const preview = payload.preview || {};
  const findings = payload.findings || {};

  const header = `
    <h3>${record.title || record.record_id || "Preview"}</h3>
    <p><strong>Type:</strong> ${record.asset_type || record.kind || "-"}</p>
    <p><strong>Tier:</strong> ${record.evidence_tier ? `Tier ${record.evidence_tier}` : "-"}</p>
    <p><strong>Path:</strong> ${record.local_path || "-"}</p>
    <p><strong>URL:</strong> ${record.url ? `<a href="${record.url}" target="_blank" rel="noreferrer">Open source</a>` : "-"}</p>
    <p>${record.evidence_rationale || ""}</p>
  `;

  const findingsHtml = buildFindingsHtml(findings);

  if (preview.type === "text") {
    panel.innerHTML = `${header}${findingsHtml}<pre>${escapeHtml(preview.text || "")}</pre>`;
    return;
  }

  if (preview.type === "image") {
    panel.innerHTML = `${header}${findingsHtml}<img src="${preview.media_url}" alt="preview image" class="media"/>`;
    return;
  }

  if (preview.type === "video") {
    panel.innerHTML = `${header}${findingsHtml}<video controls class="media"><source src="${preview.media_url}"/></video>`;
    return;
  }

  if (preview.type === "audio") {
    panel.innerHTML = `${header}${findingsHtml}<audio controls class="media"><source src="${preview.media_url}"/></audio>`;
    return;
  }

  if (preview.type === "pdf") {
    panel.innerHTML = `${header}${findingsHtml}<iframe src="${preview.media_url}" class="pdf-frame"></iframe>`;
    return;
  }

  if (preview.type === "external") {
    panel.innerHTML = `${header}${findingsHtml}<p>No local file available. Use source link above.</p>`;
    return;
  }

  panel.innerHTML = `${header}${findingsHtml}<p>Binary file preview is not supported inline.</p>`;
}

function buildFindingsHtml(findings) {
  const entities = findings.entities || [];
  const events = findings.events || [];
  if (!entities.length && !events.length) return "";

  let html = `<div class="findings">`;

  if (entities.length) {
    const grouped = {};
    for (const e of entities) {
      if (!grouped[e.entity_type]) grouped[e.entity_type] = [];
      grouped[e.entity_type].push(e);
    }
    html += `<details class="findings-section" open><summary>Extracted Entities (${entities.length})</summary><div class="findings-body">`;
    for (const [type, items] of Object.entries(grouped)) {
      html += `<div class="findings-group"><span class="etype-badge">${escapeHtml(type)}</span>`;
      html += items.map(
        (e) => `<span class="finding-chip" title="${escapeHtml(e.entity_text || e.normalized_text)} (conf ${Number(e.confidence).toFixed(2)})">${escapeHtml(e.normalized_text)} <small>${e.occurrences}×</small></span>`,
      ).join("");
      html += `</div>`;
    }
    html += `</div></details>`;
  }

  if (events.length) {
    html += `<details class="findings-section"><summary>Extracted Events (${events.length})</summary><div class="findings-body">`;
    for (const ev of events) {
      html += `<div class="event-row"><span class="etype-badge">${escapeHtml(ev.event_type)}</span>`;
      if (ev.event_date) html += ` <span class="event-date">${escapeHtml(ev.event_date)}</span>`;
      if (ev.event_text) html += ` — <span class="event-desc">${escapeHtml(ev.event_text.slice(0, 200))}</span>`;
      html += `</div>`;
    }
    html += `</div></details>`;
  }

  html += `</div>`;
  return html;
}

function escapeHtml(input) {
  return input
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function refreshResults() {
  const payload = await fetchRecords();
  renderTable(payload.items, payload.total, payload.page, payload.page_size);
}

function readStateFromControls() {
  state.q = $("q").value.trim();
  state.kind = $("kind").value;
  state.asset_type = $("asset_type").value;
  state.status = $("status").value;
  state.source_domain = $("source_domain").value;
  state.evidence_tier = $("evidence_tier").value;
  state.discovered_from = $("discovered_from").value;
  state.discovered_to = $("discovered_to").value;
  state.page = 1;
}

async function init() {
  const summary = await fetchSummary();
  wireFilters(summary);
  const insights = await fetchInsights();
  renderInsights(insights);
  await renderEntityBrowse();

  $("apply").addEventListener("click", async () => {
    readStateFromControls();
    await refreshResults();
  });

  $("reset").addEventListener("click", async () => {
    state.q = "";
    state.kind = "";
    state.asset_type = "";
    state.status = "";
    state.source_domain = "";
    state.evidence_tier = "";
    state.discovered_from = "";
    state.discovered_to = "";
    state.page = 1;

    $("q").value = "";
    $("kind").value = "";
    $("asset_type").value = "";
    $("status").value = "";
    $("source_domain").value = "";
    $("evidence_tier").value = "";
    $("discovered_from").value = "";
    $("discovered_to").value = "";

    await refreshResults();
  });

  $("prev").addEventListener("click", async () => {
    if (state.page > 1) {
      state.page -= 1;
      await refreshResults();
    }
  });

  $("next").addEventListener("click", async () => {
    state.page += 1;
    await refreshResults();
  });

  $("q").addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      readStateFromControls();
      await refreshResults();
    }
  });

  await refreshResults();
}

init().catch((error) => {
  $("preview").innerHTML = `<div class="empty">Dashboard error: ${escapeHtml(String(error))}</div>`;
});
