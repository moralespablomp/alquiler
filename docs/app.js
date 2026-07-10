const state = { properties: [], filtered: [], config: { filters: {}, portals: {}, browser: {} } };

const money = (value, currency = "ARS") => value == null ? "Precio no informado" : new Intl.NumberFormat("es-AR", { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
const valueOrDash = (value, suffix = "") => value == null ? "—" : `${value}${suffix}`;

function renderSummary(payload) {
  const filters = payload.filters || {};
  document.querySelector("#summary").innerHTML = `
    <article><strong>${payload.total || 0}</strong><span>resultados</span></article>
    <article><strong>${(filters.zones || []).join(", ") || "Sin zonas"}</strong><span>zonas</span></article>
    <article><strong>${money(filters.max_price)}</strong><span>precio máximo</span></article>`;
}

function card(p) {
  const image = p.image ? `<img src="${p.image}" alt="" loading="lazy" onerror="this.remove()">` : `<div class="image-placeholder">Sin imagen</div>`;
  const parking = p.parking === true ? "Sí" : p.parking === false ? "No" : "—";
  return `<article class="property-card">${image}<div class="property-content">
    <div class="property-heading"><div><p class="source">${p.source}</p><h2>${p.title}</h2><p class="location">${p.location || "Ubicación no informada"}</p></div><span class="score">${p.condition_score}/100</span></div>
    <p class="price">${money(p.price, p.currency)}</p>
    <dl><div><dt>Ambientes</dt><dd>${valueOrDash(p.rooms)}</dd></div><div><dt>Superficie</dt><dd>${valueOrDash(p.area_m2, " m²")}</dd></div><div><dt>Cochera</dt><dd>${parking}</dd></div><div><dt>Antigüedad</dt><dd>${valueOrDash(p.age_years, " años")}</dd></div></dl>
    <p class="condition">${p.condition_label}</p><p class="description">${p.description || "Sin descripción disponible."}</p>
    <a class="button secondary" href="${p.url}" target="_blank" rel="noopener noreferrer">Ver publicación</a></div></article>`;
}

function render() {
  const container = document.querySelector("#results");
  container.innerHTML = state.filtered.length ? state.filtered.map(card).join("") : `<div class="empty"><h2>No hay resultados</h2><p>Guardá la configuración y ejecutá una búsqueda.</p></div>`;
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
  applyControls();
}

async function loadConfig() {
  const response = await fetch("/api/config", { cache: "no-store" });
  state.config = await response.json();
  const f = state.config.filters || {};
  document.querySelector("#zones").value = (f.zones || []).join(", ");
  document.querySelector("#min-rooms").value = f.min_rooms ?? 1;
  document.querySelector("#max-rooms").value = f.max_rooms ?? 4;
  document.querySelector("#max-price").value = f.max_price ?? 0;
  document.querySelector("#min-area").value = f.min_area_m2 ?? 0;
  document.querySelector("#portal-zonaprop").checked = !!state.config.portals?.zonaprop;
  document.querySelector("#portal-argenprop").checked = !!state.config.portals?.argenprop;
  document.querySelector("#visible-browser").checked = !state.config.browser?.headless;
}

async function saveConfig() {
  const f = state.config.filters || {};
  const payload = {
    filters: { ...f, zones: document.querySelector("#zones").value.split(",").map(x => x.trim()).filter(Boolean), min_rooms: Number(document.querySelector("#min-rooms").value), max_rooms: Number(document.querySelector("#max-rooms").value), max_price: Number(document.querySelector("#max-price").value), min_area_m2: Number(document.querySelector("#min-area").value) },
    portals: { zonaprop: document.querySelector("#portal-zonaprop").checked, argenprop: document.querySelector("#portal-argenprop").checked },
    browser: { headless: !document.querySelector("#visible-browser").checked }
  };
  const response = await fetch("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "No se pudo guardar");
  state.config = result.config;
  document.querySelector("#save-message").textContent = "Guardado";
}

async function pollStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  const status = await response.json();
  const panel = document.querySelector("#execution-panel");
  panel.hidden = false;
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

document.querySelector("#save-config").addEventListener("click", () => saveConfig().catch(e => alert(e.message)));
document.querySelector("#run-search").addEventListener("click", runSearch);
document.querySelector("#text-filter").addEventListener("input", applyControls);
document.querySelector("#sort").addEventListener("change", applyControls);
Promise.all([loadConfig(), loadResults()]);
