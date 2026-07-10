const state = {
  properties: [], filtered: [], config: { filters: {}, portals: {}, browser: {} },
  selections: {}, map: null, markers: [],
};

const money = (value, currency = "ARS") => value == null ? "Precio no informado" : new Intl.NumberFormat("es-AR", { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const selectedIds = () => Object.entries(state.selections).filter(([, value]) => value.selected).map(([id]) => id);

function renderSummary(payload) {
  const filters = payload.filters || {};
  document.querySelector("#summary").innerHTML = `
    <article><strong>${payload.total || 0}</strong><span>resultados</span></article>
    <article><strong>${(filters.zones || []).join(", ") || "Sin zonas"}</strong><span>zonas</span></article>
    <article><strong>${money(filters.max_price)}</strong><span>precio máximo</span></article>`;
}

function gallery(property) {
  const images = [...new Set([...(property.images || []), property.image].filter(Boolean))].slice(0, 8);
  if (!images.length) return `<div class="image-placeholder">Sin imagen</div>`;
  return `<div class="gallery" tabindex="0">${images.map((url, index) => `<img src="${escapeHtml(url)}" alt="Foto ${index + 1}" loading="lazy" onerror="this.remove()">`).join("")}${images.length > 1 ? `<span class="photo-count">${images.length} fotos</span>` : ""}</div>`;
}

function card(p) {
  const facts = [p.rooms != null ? `${p.rooms} amb.` : "", p.area_m2 != null ? `${p.area_m2} m²` : "", p.parking === true ? "Cochera" : "", p.age_years != null ? `${p.age_years} años` : ""].filter(Boolean);
  const selected = !!state.selections[p.id]?.selected;
  return `<article class="property-card ${selected ? "is-selected" : ""}">
    ${gallery(p)}
    <button class="favorite-button ${selected ? "active" : ""}" data-select="${escapeHtml(p.id)}" title="Guardar propiedad">${selected ? "★" : "☆"}</button>
    <div class="property-content">
      <div class="card-topline"><span class="source">${escapeHtml(p.source)}</span><span class="score">${p.condition_score}/100</span></div>
      <p class="price">${money(p.price, p.currency)}</p>
      <h2 title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</h2>
      <p class="location">${escapeHtml(p.location || "Ubicación no informada")}</p>
      <div class="facts">${facts.map(f => `<span>${escapeHtml(f)}</span>`).join("") || "<span>Sin datos adicionales</span>"}</div>
      <div class="card-footer"><span class="condition">${escapeHtml(p.condition_label)}</span><a class="button compact" href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer">Ver aviso</a></div>
    </div>
  </article>`;
}

function render() {
  const container = document.querySelector("#results");
  container.innerHTML = state.filtered.length ? state.filtered.map(card).join("") : `<div class="empty"><h2>No hay resultados</h2><p>Guardá la configuración y ejecutá una búsqueda.</p></div>`;
  container.querySelectorAll("[data-select]").forEach(button => button.addEventListener("click", () => toggleSelection(button.dataset.select)));
}

function renderSaved() {
  const selected = state.properties.filter(p => state.selections[p.id]?.selected);
  document.querySelector("#saved-count").textContent = selected.length;
  const container = document.querySelector("#saved-results");
  if (!selected.length) {
    container.innerHTML = `<div class="empty"><h2>Todavía no guardaste propiedades</h2><p>Usá la estrella de cada tarjeta para agregarlas.</p></div>`;
    return;
  }
  container.innerHTML = selected.map(p => {
    const saved = state.selections[p.id] || {};
    return `<article class="saved-card">
      <div class="saved-image">${p.image ? `<img src="${escapeHtml(p.image)}" alt="" loading="lazy">` : "Sin imagen"}</div>
      <div class="saved-body">
        <div class="saved-heading"><div><span class="source">${escapeHtml(p.source)}</span><h3>${escapeHtml(p.title)}</h3><p>${escapeHtml(p.location || "Ubicación no informada")}</p></div><strong>${money(p.price, p.currency)}</strong></div>
        <div class="saved-fields">
          <label>Estado<select data-status="${p.id}"><option ${saved.status === "Para revisar" ? "selected" : ""}>Para revisar</option><option ${saved.status === "Contactar" ? "selected" : ""}>Contactar</option><option ${saved.status === "Visita coordinada" ? "selected" : ""}>Visita coordinada</option><option ${saved.status === "Finalista" ? "selected" : ""}>Finalista</option><option ${saved.status === "Descartado" ? "selected" : ""}>Descartado</option></select></label>
          <label class="notes-field">Notas<textarea data-notes="${p.id}" placeholder="Expensas, impresión general, preguntas, fecha de visita…">${escapeHtml(saved.notes || "")}</textarea></label>
        </div>
        <div class="saved-actions"><span data-saved-message="${p.id}"></span><a class="button secondary compact" href="${escapeHtml(p.url)}" target="_blank">Abrir aviso</a><button class="button compact" data-save-note="${p.id}">Guardar cambios</button><button class="text-button danger-text" data-remove="${p.id}">Quitar</button></div>
      </div>
    </article>`;
  }).join("");
  container.querySelectorAll("[data-save-note]").forEach(btn => btn.addEventListener("click", () => saveNotes(btn.dataset.saveNote)));
  container.querySelectorAll("[data-remove]").forEach(btn => btn.addEventListener("click", () => removeSelection(btn.dataset.remove)));
}

async function toggleSelection(id) {
  const current = state.selections[id];
  if (current?.selected) return removeSelection(id);
  const payload = { selected: true, status: current?.status || "Para revisar", notes: current?.notes || "" };
  const response = await fetch(`/api/selections/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  state.selections[id] = (await response.json()).selection;
  render(); renderSaved();
}

async function saveNotes(id) {
  const status = document.querySelector(`[data-status="${id}"]`).value;
  const notes = document.querySelector(`[data-notes="${id}"]`).value;
  const response = await fetch(`/api/selections/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selected: true, status, notes }) });
  state.selections[id] = (await response.json()).selection;
  const message = document.querySelector(`[data-saved-message="${id}"]`);
  message.textContent = "Guardado";
  setTimeout(() => message.textContent = "", 1600);
  render();
}

async function removeSelection(id) {
  await fetch(`/api/selections/${encodeURIComponent(id)}`, { method: "DELETE" });
  delete state.selections[id];
  render(); renderSaved();
}

function applyControls() {
  const query = document.querySelector("#text-filter").value.toLowerCase().trim();
  const sort = document.querySelector("#sort").value;
  state.filtered = state.properties.filter(p => `${p.title} ${p.location} ${p.source} ${p.description}`.toLowerCase().includes(query));
  state.filtered.sort((a, b) => sort === "price-asc" ? (a.price ?? Infinity) - (b.price ?? Infinity) : sort === "price-desc" ? (b.price ?? -1) - (a.price ?? -1) : sort === "recent" ? new Date(b.found_at) - new Date(a.found_at) : b.condition_score - a.condition_score);
  render();
}

async function loadResults() {
  const response = await fetch("/api/results", { cache: "no-store" });
  const payload = await response.json();
  state.properties = payload.properties || [];
  state.filtered = [...state.properties];
  renderSummary(payload);
  document.querySelector("#status").textContent = payload.generated_at ? `Última actualización: ${new Date(payload.generated_at).toLocaleString("es-AR")}` : "Todavía no se ejecutó una búsqueda";
  applyControls(); renderSaved();
}

async function loadSelections() {
  state.selections = await (await fetch("/api/selections", { cache: "no-store" })).json();
  render(); renderSaved();
}

async function loadConfig() {
  state.config = await (await fetch("/api/config", { cache: "no-store" })).json();
  const f = state.config.filters || {};
  document.querySelector("#zones").value = (f.zones || []).join(", ");
  document.querySelector("#min-rooms").value = f.min_rooms ?? 1;
  document.querySelector("#max-rooms").value = f.max_rooms ?? 4;
  document.querySelector("#max-price").value = f.max_price ?? 0;
  document.querySelector("#min-area").value = f.min_area_m2 ?? 0;
  document.querySelector("#portal-zonaprop").checked = state.config.portals?.zonaprop !== false;
  document.querySelector("#portal-argenprop").checked = state.config.portals?.argenprop !== false;
  document.querySelector("#visible-browser").checked = !state.config.browser?.headless;
}

async function saveConfig() {
  const f = state.config.filters || {};
  const payload = { filters: { ...f, zones: document.querySelector("#zones").value.split(",").map(x => x.trim()).filter(Boolean), min_rooms: Number(document.querySelector("#min-rooms").value), max_rooms: Number(document.querySelector("#max-rooms").value), max_price: Number(document.querySelector("#max-price").value), min_area_m2: Number(document.querySelector("#min-area").value) }, portals: { zonaprop: document.querySelector("#portal-zonaprop").checked, argenprop: document.querySelector("#portal-argenprop").checked }, browser: { headless: !document.querySelector("#visible-browser").checked } };
  const response = await fetch("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "No se pudo guardar");
  state.config = result.config;
  document.querySelector("#save-message").textContent = "Guardado";
}

async function pollStatus() {
  const status = await (await fetch("/api/status", { cache: "no-store" })).json();
  document.querySelector("#execution-panel").hidden = false;
  document.querySelector("#execution-log").textContent = (status.log || []).join("\n");
  document.querySelector("#execution-title").textContent = status.running ? "Buscando…" : status.last_success ? "Búsqueda terminada" : status.last_error ? "La búsqueda falló" : "Estado";
  document.querySelector("#run-search").disabled = status.running;
  if (status.running) setTimeout(pollStatus, 1200); else await loadResults();
}

async function runSearch() {
  try {
    await saveConfig();
    const response = await fetch("/api/run", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo iniciar");
    pollStatus();
  } catch (error) { alert(error.message); }
}

function initMap() {
  if (state.map) return;
  state.map = L.map("map").setView([-34.67, -58.58], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(state.map);
}

async function compareMap() {
  initMap();
  document.querySelector("#map-status").textContent = "Calculando ubicaciones y recorridos…";
  const ids = selectedIds();
  const response = await fetch("/api/map/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
  const data = await response.json();
  state.markers.forEach(marker => marker.remove()); state.markers = [];
  const hospitalMarker = L.marker([data.hospital.lat, data.hospital.lon]).addTo(state.map).bindPopup(`<strong>${escapeHtml(data.hospital.name)}</strong><br>${escapeHtml(data.hospital.address)}`);
  state.markers.push(hospitalMarker);
  const valid = data.properties.filter(item => item.lat && item.lon);
  valid.forEach((item, index) => {
    const marker = L.marker([item.lat, item.lon]).addTo(state.map).bindPopup(`<strong>${index + 1}. ${escapeHtml(item.title)}</strong><br>${escapeHtml(item.address)}<br>${item.distance_km ?? "—"} km · ${item.duration_min ?? "—"} min`);
    state.markers.push(marker);
  });
  if (state.markers.length) state.map.fitBounds(L.featureGroup(state.markers).getBounds().pad(0.18));
  document.querySelector("#map-status").textContent = ids.length ? `${valid.length} de ${ids.length} seleccionadas ubicadas` : `${valid.length} propiedades ubicadas`;
  document.querySelector("#comparison-list").innerHTML = data.properties.sort((a, b) => (a.duration_min ?? Infinity) - (b.duration_min ?? Infinity)).map((item, index) => item.error ? `<article class="comparison-item error"><strong>${escapeHtml(item.address)}</strong><span>No se pudo ubicar con precisión.</span></article>` : `<article class="comparison-item"><span class="rank">${index + 1}</span><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.address)}</p><small>${money(item.price, item.currency)}</small></div><div class="trip"><strong>${item.duration_min} min</strong><span>${item.distance_km} km</span></div></article>`).join("");
  setTimeout(() => state.map.invalidateSize(), 100);
}

function switchTab(id) {
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === id));
  document.querySelectorAll(".tab-view").forEach(view => view.classList.toggle("active", view.id === id));
  if (id === "map-view") { initMap(); setTimeout(() => state.map.invalidateSize(), 100); }
}

document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
document.querySelector("#save-config").addEventListener("click", () => saveConfig().catch(e => alert(e.message)));
document.querySelector("#run-search").addEventListener("click", runSearch);
document.querySelector("#compare-map").addEventListener("click", compareMap);
document.querySelector("#text-filter").addEventListener("input", applyControls);
document.querySelector("#sort").addEventListener("change", applyControls);
Promise.all([loadConfig(), loadResults(), loadSelections()]);
