const state = { properties: [], filtered: [], config: { filters: {}, portals: {}, browser: {} } };

const money = (value, currency = "ARS") => value == null ? "Precio no informado" : new Intl.NumberFormat("es-AR", { style: "currency", currency, maximumFractionDigits: 0 }).format(value);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);

function renderSummary(payload) {
  const filters = payload.filters || {};
  document.querySelector("#summary").innerHTML = `
    <article><strong>${payload.total || 0}</strong><span>resultados encontrados</span></article>
    <article><strong>${escapeHtml((filters.zones || []).join(", ") || "Sin zonas")}</strong><span>zonas incluidas</span></article>
    <article><strong>${money(filters.max_price)}</strong><span>precio máximo configurado</span></article>`;
}

function gallery(property) {
  const images = [...new Set([...(property.images || []), property.image].filter(Boolean))].slice(0, 8);
  if (!images.length) return `<div class="image-placeholder">Sin imagen</div>`;
  const slides = images.map((url, index) => `<img src="${escapeHtml(url)}" alt="Foto ${index + 1} de ${escapeHtml(property.title)}" loading="lazy" onerror="this.remove()">`).join("");
  return `<div class="media-strip" tabindex="0">${slides}${images.length > 1 ? `<span class="photo-count">${images.length} fotos</span>` : ""}</div>`;
}

function card(p) {
  const facts = [
    p.rooms != null ? `${p.rooms} amb.` : "",
    p.area_m2 != null ? `${p.area_m2} m²` : "",
    p.parking === true ? "Cochera" : "",
    p.age_years != null ? `${p.age_years} años` : "",
  ].filter(Boolean);

  return `<article class="property-card">
    ${gallery(p)}
    <div class="property-content">
      <div class="property-topline"><span class="source">${escapeHtml(p.source)}</span><span class="score">${p.condition_score}/100</span></div>
      <h2 title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</h2>
      <p class="location">${escapeHtml(p.location || "Ubicación no informada")}</p>
      <p class="price">${money(p.price, p.currency)}</p>
      <div class="property-facts">${facts.map(fact => `<span>${escapeHtml(fact)}</span>`).join("") || "<span>Sin datos adicionales</span>"}</div>
      <div class="card-footer"><span class="condition">${escapeHtml(p.condition_label)}</span><a class="card-link" href="${escapeHtml(p.url)}" target="_blank" rel="noopener noreferrer">Ver aviso →</a></div>
    </div>
  </article>`;
}

function render() {
  const container = document.querySelector("#results");
  container.innerHTML = state.filtered.length ? state.filtered.map(card).join("") : `<div class="empty"><h2>No hay resultados</h2><p>Ajustá la configuración y ejecutá una nueva búsqueda.</p></div>`;
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
  document.querySelector("#status").textContent = payload.generated_at ? `Actualizado ${new Date(payload.generated_at).toLocaleString("es-AR")}` : "Todavía no se ejecutó una búsqueda";
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
  document.querySelector("#portal-zonaprop").checked = state.config.portals?.zonaprop !== false;
  document.querySelector("#portal-argenprop").checked = state.config.portals?.argenprop !== false;
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
  const message = document.querySelector("#save-message");
  message.textContent = "Cambios guardados";
  window.setTimeout(() => message.textContent = "", 2200);
}

async function pollStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  const status = await response.json();
  const panel = document.querySelector("#execution-panel");
  panel.hidden = false;
  document.querySelector("#execution-log").textContent = (status.log || []).join("\n");
  document.querySelector("#execution-title").textContent = status.running ? "Buscando propiedades" : status.last_success ? "Búsqueda terminada" : status.last_error ? "La búsqueda falló" : "Estado";
  const button = document.querySelector("#run-search");
  button.disabled = status.running;
  button.textContent = status.running ? "Buscando…" : "Buscar ahora";
  if (status.running) setTimeout(pollStatus, 1200); else await loadResults();
}

async function runSearch() {
  try {
    await saveConfig();
    document.querySelector("#settings-panel").classList.remove("open");
    const response = await fetch("/api/run", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo iniciar");
    pollStatus();
  } catch (error) { alert(error.message); }
}

const settings = document.querySelector("#settings-panel");
document.querySelector("#toggle-settings").addEventListener("click", () => settings.classList.toggle("open"));
document.querySelector("#close-settings").addEventListener("click", () => settings.classList.remove("open"));
document.querySelector("#save-config").addEventListener("click", () => saveConfig().catch(e => alert(e.message)));
document.querySelector("#run-search").addEventListener("click", runSearch);
document.querySelector("#text-filter").addEventListener("input", applyControls);
document.querySelector("#sort").addEventListener("change", applyControls);
Promise.all([loadConfig(), loadResults()]);
