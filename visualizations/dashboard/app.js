const state = {
  q: "",
  kind: "",
  asset_type: "",
  status: "",
  source_domain: "",
  evidence_tier: "",
  parse_status: "",
  discovered_from: "",
  discovered_to: "",
  page: 1,
  page_size: 50,
  currentPreviewRecordId: "",
  breadcrumbs: [],
  theme: "neon_ops",
};

const THEME_STORAGE_KEY = "pht_dashboard_theme";

function $(id) {
  return document.getElementById(id);
}

function showModal() {
  const modal = $("preview-modal");
  if (modal) modal.classList.remove("hidden");
}

function hideModal() {
  const modal = $("preview-modal");
  if (modal) modal.classList.add("hidden");
}

function setModalTitle(title) {
  const titleEl = $("preview-title");
  if (titleEl) titleEl.textContent = title;
}

function escapeHtml(input) {
  return String(input)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function optionMarkup(value, label) {
  return `<option value="${value}">${label}</option>`;
}

function applyTheme(themeName) {
  const validThemes = new Set(["neon_ops", "black_ops", "night_vision"]);
  const selected = validThemes.has(themeName) ? themeName : "neon_ops";
  state.theme = selected;
  document.documentElement.setAttribute("data-theme", selected);

  const selectEl = $("theme-select");
  if (selectEl) {
    selectEl.value = selected;
  }

  localStorage.setItem(THEME_STORAGE_KEY, selected);
}

function pushBreadcrumb(label, filters = {}) {
  const normalized = {
    q: filters.q ?? state.q,
    kind: filters.kind ?? state.kind,
    asset_type: filters.asset_type ?? state.asset_type,
    status: filters.status ?? state.status,
    source_domain: filters.source_domain ?? state.source_domain,
    evidence_tier: filters.evidence_tier ?? state.evidence_tier,
    parse_status: filters.parse_status ?? state.parse_status,
    discovered_from: filters.discovered_from ?? state.discovered_from,
    discovered_to: filters.discovered_to ?? state.discovered_to,
  };

  const last = state.breadcrumbs[state.breadcrumbs.length - 1];
  if (last && last.label === label && JSON.stringify(last.filters) === JSON.stringify(normalized)) {
    return;
  }

  state.breadcrumbs.push({ label, filters: normalized });
  if (state.breadcrumbs.length > 8) {
    state.breadcrumbs = state.breadcrumbs.slice(state.breadcrumbs.length - 8);
  }
  renderBreadcrumbs();
}

function renderBreadcrumbs() {
  const container = $("breadcrumbs");
  if (!container) return;

  if (!state.breadcrumbs.length) {
    container.innerHTML = `<span class="breadcrumb-empty">No breadcrumb context yet.</span>`;
    return;
  }

  container.innerHTML = state.breadcrumbs
    .map((crumb, idx) => `<button class="breadcrumb-item" data-index="${idx}">${escapeHtml(crumb.label)}</button>`)
    .join('<span class="breadcrumb-sep">></span>');

  container.querySelectorAll(".breadcrumb-item").forEach((button) => {
    button.addEventListener("click", async () => {
      const idx = Number(button.getAttribute("data-index"));
      const crumb = state.breadcrumbs[idx];
      if (!crumb) return;

      state.breadcrumbs = state.breadcrumbs.slice(0, idx + 1);
      state.q = crumb.filters.q;
      state.kind = crumb.filters.kind;
      state.asset_type = crumb.filters.asset_type;
      state.status = crumb.filters.status;
      state.source_domain = crumb.filters.source_domain;
      state.evidence_tier = crumb.filters.evidence_tier;
      state.parse_status = crumb.filters.parse_status;
      state.discovered_from = crumb.filters.discovered_from;
      state.discovered_to = crumb.filters.discovered_to;
      state.page = 1;

      $("q").value = state.q;
      $("kind").value = state.kind;
      $("asset_type").value = state.asset_type;
      $("status").value = state.status;
      $("source_domain").value = state.source_domain;
      $("evidence_tier").value = state.evidence_tier;
      $("discovered_from").value = state.discovered_from;
      $("discovered_to").value = state.discovered_to;

      renderBreadcrumbs();
      await refreshResults();
    });
  });
}

async function openRecordInNewTab(recordId) {
  const payload = await fetchPreview(recordId);
  if (payload.error) {
    return;
  }

  const record = payload.record || {};
  const preview = payload.preview || {};
  const newTabUrl = preview.media_url || record.url || "";

  if (newTabUrl) {
    window.open(newTabUrl, "_blank", "noopener,noreferrer");
    return;
  }

  if (preview.type === "text" && preview.text) {
    const textWindow = window.open("", "_blank", "noopener,noreferrer");
    if (textWindow) {
      textWindow.document.write(`<pre>${escapeHtml(preview.text)}</pre>`);
      textWindow.document.close();
    }
  }
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

async function fetchDebrief(assetId) {
  const params = new URLSearchParams({ asset_id: String(assetId) });
  const response = await fetch(`/api/debrief?${params.toString()}`);
  return response.json();
}

async function fetchCorrelations(assetId, correlationType = "entity") {
  const params = new URLSearchParams({ asset_id: String(assetId), type: correlationType });
  const response = await fetch(`/api/correlations?${params.toString()}`);
  return response.json();
}

async function fetchTimeline(dateFrom = "", dateTo = "") {
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const response = await fetch(`/api/timeline?${params.toString()}`);
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
      <td class="actions">
        <button data-record-id="${item.record_id}" data-action="preview">Preview</button>
        <button data-record-id="${item.record_id}" data-action="tab" class="ghost action-tab">Open Tab</button>
      </td>
    `;
    tbody.appendChild(tr);
  }

  const maxPage = Math.max(1, Math.ceil(total / pageSize));
  $("page").textContent = `Page ${page} of ${maxPage} • ${total} results`;
  $("prev").disabled = page <= 1;
  $("next").disabled = page >= maxPage;

  tbody.querySelectorAll("button[data-record-id][data-action='preview']").forEach((button) => {
    button.addEventListener("click", async () => {
      const recordId = button.getAttribute("data-record-id");
      if (!recordId) return;
      await renderPreview(recordId);
      pushBreadcrumb(`Record: ${recordId}`);
    });
  });

  tbody.querySelectorAll("button[data-record-id][data-action='tab']").forEach((button) => {
    button.addEventListener("click", async () => {
      const recordId = button.getAttribute("data-record-id");
      if (!recordId) return;
      await openRecordInNewTab(recordId);
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
        `<button class="insight-row parse-filter" data-status="${escapeHtml(String(key))}"><span class="insight-key">${escapeHtml(String(key))}</span><span class="insight-value">${escapeHtml(String(value))}</span></button>`,
    );
  parseStatusEl.innerHTML = parseRows.length
    ? parseRows.join("")
    : `<div class="insight-row"><span class="insight-key">No parse status data</span><span class="insight-value">-</span></div>`;

  parseStatusEl.querySelectorAll(".parse-filter").forEach((button) => {
    button.addEventListener("click", async () => {
      const statusValue = button.getAttribute("data-status") || "";
      state.parse_status = statusValue;
      state.q = "";
      state.kind = "parsed_text";
      state.page = 1;
      $("q").value = "";
      $("kind").value = "parsed_text";
      parseStatusEl.querySelectorAll(".parse-filter").forEach((row) => row.classList.remove("active"));
      button.classList.add("active");
      pushBreadcrumb(`Parse Status: ${statusValue}`, { parse_status: statusValue, kind: "parsed_text", q: "" });
      await refreshResults();
    });
  });

  parseStatusEl.querySelectorAll(".parse-filter").forEach((row) => {
    const current = (row.getAttribute("data-status") || "").toLowerCase();
    const active = Boolean(state.parse_status) && current === state.parse_status.toLowerCase();
    row.classList.toggle("active", active);
  });

  const topEntities = payload.top_entities || [];
  // Collect unique entity types for filter chips
  const entityTypes = [...new Set(topEntities.map((r) => r.entity_type))];
  const chipHtml = entityTypes.map(
    (t) =>
      `<button class="entity-chip" data-type="${escapeHtml(t)}">${escapeHtml(t)}</button>`,
  ).join("");
  const entityRows = topEntities.map(
    (row) =>
      `<button class="insight-row entity-row" data-type="${escapeHtml(String(row.entity_type || ""))}" data-entity-text="${escapeHtml(String(row.normalized_text || ""))}"><span class="insight-key"><span class="etype-badge">${escapeHtml(String(row.entity_type || "?"))}</span> ${escapeHtml(String(row.normalized_text || ""))}</span><span class="insight-value">${escapeHtml(String(row.total_occurrences || 0))}</span></button>`,
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

  topEntitiesEl.querySelectorAll(".entity-row").forEach((row) => {
    row.addEventListener("click", async () => {
      const entityText = row.getAttribute("data-entity-text") || "";
      if (!entityText) return;
      state.q = entityText;
      state.page = 1;
      $("q").value = entityText;
      pushBreadcrumb(`Entity: ${entityText}`, { q: entityText });
      await refreshResults();
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
  state.currentPreviewRecordId = recordId;

  if (payload.error) {
    panel.innerHTML = `<div class="empty">${escapeHtml(payload.error)}</div>`;
    setModalTitle("Preview - Error");
    showModal();
    return;
  }

  const record = payload.record || {};
  const preview = payload.preview || {};
  const findings = payload.findings || {};

  setModalTitle(record.title || record.record_id || "Preview");

  const header = `
    <div class="preview-header">
      <h3>${escapeHtml(record.title || record.record_id || "Preview")}</h3>
      <div class="preview-meta">
        <p><strong>Type:</strong> ${escapeHtml(record.asset_type || record.kind || "-")}</p>
        <p><strong>Tier:</strong> ${record.evidence_tier ? `Tier ${record.evidence_tier}` : "-"}</p>
        <p><strong>Path:</strong> <code>${escapeHtml(record.local_path || "-")}</code></p>
        ${record.url ? `<p><strong>Source:</strong> <a href="${escapeHtml(record.url)}" target="_blank" rel="noreferrer" class="source-link">Open original source</a></p>` : ""}
        ${record.evidence_rationale ? `<p><strong>Rationale:</strong> ${escapeHtml(record.evidence_rationale)}</p>` : ""}
      </div>
    </div>
  `;

  const findingsHtml = buildFindingsHtml(findings);

  // Extract asset_id if this is an asset record
  let assetIdForCorrelation = null;
  if (record.record_id && record.record_id.startsWith("asset:")) {
    assetIdForCorrelation = parseInt(record.record_id.split(":")[1], 10);
  }

  let extraPanels = "";
  if (assetIdForCorrelation) {
    extraPanels = `
      <div id="debrief-panel" class="analysis-panel"></div>
      <div id="correlations-panel" class="analysis-panel"></div>
      <div id="timeline-panel" class="analysis-panel"></div>
    `;
  }

  if (preview.type === "text") {
    panel.innerHTML = `${header}${findingsHtml}${extraPanels}<pre>${escapeHtml(preview.text || "")}</pre>`;
    if (assetIdForCorrelation) {
      await renderDebrief(assetIdForCorrelation);
      await renderCorrelations(assetIdForCorrelation);
      await renderTimeline();
    }
    showModal();
    attachEntityLinkListeners();
    return;
  }

  if (preview.type === "image") {
    panel.innerHTML = `${header}${findingsHtml}${extraPanels}<img src="${escapeHtml(preview.media_url)}" alt="preview image" class="media"/>`;
    if (assetIdForCorrelation) {
      await renderDebrief(assetIdForCorrelation);
      await renderCorrelations(assetIdForCorrelation);
      await renderTimeline();
    }
    showModal();
    attachEntityLinkListeners();
    return;
  }

  if (preview.type === "video") {
    panel.innerHTML = `${header}${findingsHtml}${extraPanels}<video controls class="media"><source src="${escapeHtml(preview.media_url)}"/></video>`;
    if (assetIdForCorrelation) {
      await renderDebrief(assetIdForCorrelation);
      await renderCorrelations(assetIdForCorrelation);
      await renderTimeline();
    }
    showModal();
    attachEntityLinkListeners();
    return;
  }

  if (preview.type === "audio") {
    panel.innerHTML = `${header}${findingsHtml}${extraPanels}<audio controls class="media"><source src="${escapeHtml(preview.media_url)}"/></audio>`;
    if (assetIdForCorrelation) {
      await renderDebrief(assetIdForCorrelation);
      await renderCorrelations(assetIdForCorrelation);
      await renderTimeline();
    }
    showModal();
    attachEntityLinkListeners();
    return;
  }

  if (preview.type === "pdf") {
    panel.innerHTML = `${header}${findingsHtml}${extraPanels}<iframe src="${escapeHtml(preview.media_url)}" class="pdf-frame"></iframe>`;
    if (assetIdForCorrelation) {
      await renderDebrief(assetIdForCorrelation);
      await renderCorrelations(assetIdForCorrelation);
      await renderTimeline();
    }
    showModal();
    attachEntityLinkListeners();
    return;
  }

  if (preview.type === "external") {
    panel.innerHTML = `${header}${findingsHtml}${extraPanels}<p>No local file available. Use source link above.</p>`;
    if (assetIdForCorrelation) {
      await renderDebrief(assetIdForCorrelation);
      await renderCorrelations(assetIdForCorrelation);
      await renderTimeline();
    }
    showModal();
    attachEntityLinkListeners();
    return;
  }

  panel.innerHTML = `${header}${findingsHtml}${extraPanels}<p>Binary file preview is not supported inline.</p>`;
  if (assetIdForCorrelation) {
    await renderDebrief(assetIdForCorrelation);
    await renderCorrelations(assetIdForCorrelation);
    await renderTimeline();
  }
  showModal();
  attachEntityLinkListeners();
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
        (e) => `<span class="finding-chip entity-link" data-entity-type="${escapeHtml(type)}" data-entity-text="${escapeHtml(e.normalized_text)}" title="Click to search for this entity">${escapeHtml(e.normalized_text)} <small>${e.occurrences}×</small></span>`,
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

function attachEntityLinkListeners() {
  const preview = $("preview");
  if (!preview) return;

  preview.querySelectorAll(".entity-link").forEach((link) => {
    link.addEventListener("click", async () => {
      const entityType = link.getAttribute("data-entity-type");
      const entityText = link.getAttribute("data-entity-text");
      if (entityType && entityText) {
        // Filter results by searching for this entity
        state.q = entityText;
        $("q").value = entityText;
        state.page = 1;
        pushBreadcrumb(`Entity: ${entityText}`, { q: entityText });
        hideModal();
        await refreshResults();
      }
    });
  });
}

async function renderDebrief(assetId) {
  const debriefPanel = $("debrief-panel");
  if (!debriefPanel) return "";

  debriefPanel.innerHTML = `<div class="empty">Loading debrief...</div>`;
  const data = await fetchDebrief(assetId);

  if (data.error) {
    debriefPanel.innerHTML = `<div class="empty">Error: ${escapeHtml(data.error)}</div>`;
    return "";
  }

  const summary = data.summary || {};
  const topEntities = data.top_entities || [];
  const allEvents = data.all_events || [];

  let html = `<h4>Debrief Summary</h4>`;
  html += `<div class="debrief-stats">`;
  html += `<div class="stat-row"><span class="stat-label">Total Entities:</span><span class="stat-value">${summary.total_entities || 0}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Total Events:</span><span class="stat-value">${summary.total_events || 0}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Avg Entity Confidence:</span><span class="stat-value">${(summary.avg_entity_confidence || 0).toFixed(2)}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Avg Event Confidence:</span><span class="stat-value">${(summary.avg_event_confidence || 0).toFixed(2)}</span></div>`;
  html += `<div class="stat-row"><span class="stat-label">Confidence Tier:</span><span class="stat-value">${summary.confidence_tier || "-"}</span></div>`;
  html += `</div>`;

  if (topEntities.length) {
    html += `<h5>Key Entities</h5>`;
    for (const e of topEntities) {
      html += `<div class="debrief-entity"><span class="etype-badge">${escapeHtml(e.entity_type || "?")}</span> ${escapeHtml(e.normalized_text)} <small>(conf: ${Number(e.confidence || 0).toFixed(2)}, ${e.occurrences || 0}×)</small></div>`;
    }
  }

  if (allEvents.length) {
    html += `<h5>Key Events</h5>`;
    for (const ev of allEvents.slice(0, 5)) {
      html += `<div class="debrief-event"><span class="etype-badge">${escapeHtml(ev.event_type || "?")}</span> ${ev.event_date || "?"} — ${escapeHtml((ev.event_text || "").slice(0, 150))}</div>`;
    }
    if (allEvents.length > 5) html += `<div class="empty-small">... and ${allEvents.length - 5} more events</div>`;
  }

  debriefPanel.innerHTML = html;
  return html;
}

async function renderCorrelations(assetId) {
  const corrPanel = $("correlations-panel");
  if (!corrPanel) return "";

  corrPanel.innerHTML = `<div class="empty">Loading correlations...</div>`;
  const data = await fetchCorrelations(assetId, "entity");

  if (data.error) {
    corrPanel.innerHTML = `<div class="empty">Error: ${escapeHtml(data.error)}</div>`;
    return "";
  }

  const correlations = data.correlations || [];

  if (!correlations.length) {
    corrPanel.innerHTML = `<div class="empty-small">No correlations found.</div>`;
    return "";
  }

  let html = `<h4>Related Files</h4>`;
  for (const corr of correlations.slice(0, 10)) {
    const relatedRecordId = `asset:${corr.asset_id}`;
    html += `<div class="correlation-row">`;
    html += `<div class="corr-head">`;
    html += `<div class="corr-title"><span class="asset-link" data-record-id="${escapeHtml(relatedRecordId)}" title="Click to preview">${escapeHtml(corr.title || `Asset ${corr.asset_id}`)}</span></div>`;
    html += `<button class="corr-open" data-record-id="${escapeHtml(relatedRecordId)}">Open</button>`;
    html += `</div>`;
    html += `<div class="corr-badge" style="width: ${Math.floor(corr.link_strength * 100)}%; background: hsl(${Math.floor(120 - corr.link_strength * 120)}, 70%, 50%);">`;
    html += `${escapeHtml(corr.why_linked)} (${(corr.link_strength * 100).toFixed(0)}%)`;
    html += `</div></div>`;
  }
  if (correlations.length > 10) html += `<div class="empty-small">... and ${correlations.length - 10} more</div>`;

  corrPanel.innerHTML = html;

  corrPanel.querySelectorAll(".corr-open").forEach((button) => {
    button.addEventListener("click", async () => {
      const recordId = button.getAttribute("data-record-id");
      if (recordId) {
        await renderPreview(recordId);
        pushBreadcrumb(`Related: ${recordId}`);
      }
    });
  });

  corrPanel.querySelectorAll(".asset-link").forEach((link) => {
    link.addEventListener("click", async () => {
      const recordId = link.getAttribute("data-record-id");
      if (recordId) {
        await renderPreview(recordId);
        pushBreadcrumb(`Related: ${recordId}`);
      }
    });
  });

  return html;
}

async function renderTimeline() {
  const timelinePanel = $("timeline-panel");
  if (!timelinePanel) return "";

  timelinePanel.innerHTML = `<div class="empty">Loading timeline...</div>`;
  const data = await fetchTimeline();

  if (data.error) {
    timelinePanel.innerHTML = `<div class="empty">Error: ${escapeHtml(data.error)}</div>`;
    return "";
  }

  const events = data.events || [];

  if (!events.length) {
    timelinePanel.innerHTML = `<div class="empty-small">No events in timeline.</div>`;
    return "";
  }

  let html = `<h4>Timeline</h4>`;
  let currentDate = "";
  for (const evt of events.slice(0, 20)) {
    if (evt.event_date !== currentDate) {
      if (currentDate) html += `</div>`;
      currentDate = evt.event_date;
      html += `<div class="timeline-date">${escapeHtml(evt.event_date || "Unknown date")}</div>`;
    }
    const timelineRecordId = `asset:${evt.asset_id}`;
    html += `<div class="timeline-event">`;
    html += `<span class="etype-badge">${escapeHtml(evt.event_type || "?")}</span> `;
    html += `<button class="timeline-source-link" data-record-id="${escapeHtml(timelineRecordId)}" title="Open source asset preview">${escapeHtml(evt.asset_title || "Unknown")}</button> `;
    html += `${escapeHtml(evt.event_text || "")}`;
    html += `</div>`;
  }
  if (currentDate) html += `</div>`;
  if (events.length > 20) html += `<div class="empty-small">... and ${events.length - 20} more events</div>`;

  timelinePanel.innerHTML = html;

  timelinePanel.querySelectorAll(".timeline-source-link").forEach((button) => {
    button.addEventListener("click", async () => {
      const recordId = button.getAttribute("data-record-id");
      if (recordId) {
        await renderPreview(recordId);
        pushBreadcrumb(`Timeline Source: ${recordId}`);
      }
    });
  });

  return html;
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
  if (state.parse_status && state.kind !== "parsed_text") {
    state.parse_status = "";
  }
  state.discovered_from = $("discovered_from").value;
  state.discovered_to = $("discovered_to").value;
  state.page = 1;
}

async function init() {
  const savedTheme = localStorage.getItem(THEME_STORAGE_KEY) || "neon_ops";
  applyTheme(savedTheme);

  const themeSelect = $("theme-select");
  if (themeSelect) {
    themeSelect.addEventListener("change", () => {
      applyTheme(themeSelect.value);
    });
  }

  const summary = await fetchSummary();
  wireFilters(summary);
  const insights = await fetchInsights();
  renderInsights(insights);
  await renderEntityBrowse();

  // Modal close handlers
  const modal = $("preview-modal");
  const overlay = modal ? modal.querySelector(".modal-overlay") : null;
  const closeBtn = $("modal-close");

  if (overlay) {
    overlay.addEventListener("click", () => hideModal());
  }
  if (closeBtn) {
    closeBtn.addEventListener("click", () => hideModal());
  }

  const openTabBtn = $("open-tab");
  if (openTabBtn) {
    openTabBtn.addEventListener("click", async () => {
      if (!state.currentPreviewRecordId) return;
      await openRecordInNewTab(state.currentPreviewRecordId);
    });
  }

  const clearBreadcrumbs = $("clear-breadcrumbs");
  if (clearBreadcrumbs) {
    clearBreadcrumbs.addEventListener("click", () => {
      state.breadcrumbs = [];
      renderBreadcrumbs();
    });
  }

  // Close modal on Escape key
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && !modal.classList.contains("hidden")) {
      hideModal();
    }
  });

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
    state.parse_status = "";
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

    const parseStatusEl = $("parse-status");
    if (parseStatusEl) {
      parseStatusEl.querySelectorAll(".parse-filter").forEach((row) => row.classList.remove("active"));
    }

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
  renderBreadcrumbs();
}

init().catch((error) => {
  const panel = $("preview");
  if (panel) panel.innerHTML = `<div class="empty">Dashboard error: ${escapeHtml(String(error))}</div>`;
});
