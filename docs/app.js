const state = { properties: [], filtered: [] };

const money = (value, currency = "ARS") => {
  if (value === null || value === undefined) return "Precio no informado";
  return new Intl.NumberFormat("es-AR", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
};

const valueOrDash = (value, suffix = "") =>
  value === null || value === undefined ? "—" : `${value}${suffix}`;

function renderSummary(payload) {
  const filters = payload.filters || {};
  const zones = (filters.zones || []).join(", ") || "Sin zonas configuradas";
  document.querySelector("#summary").innerHTML = `
    <article><strong>${payload.total || 0}</strong><span>resultados</span></article>
    <article><strong>${zones}</strong><span>zonas buscadas</span></article>
    <article><strong>${money(filters.max_price)}</strong><span>precio máximo</span></article>
  `;
}

function card(property) {
  const image = property.image
    ? `<img src="${property.image}" alt="" loading="lazy" onerror="this.remove()">`
    : `<div class="image-placeholder">Sin imagen</div>`;
  const parking = property.parking === true ? "Sí" : property.parking === false ? "No" : "—";

  return `
    <article class="property-card">
      ${image}
      <div class="property-content">
        <div class="property-heading">
          <div>
            <p class="source">${property.source}</p>
            <h2>${property.title}</h2>
            <p class="location">${property.location || "Ubicación no informada"}</p>
          </div>
          <span class="score">${property.condition_score}/100</span>
        </div>
        <p class="price">${money(property.price, property.currency)}</p>
        <dl>
          <div><dt>Ambientes</dt><dd>${valueOrDash(property.rooms)}</dd></div>
          <div><dt>Superficie</dt><dd>${valueOrDash(property.area_m2, " m²")}</dd></div>
          <div><dt>Cochera</dt><dd>${parking}</dd></div>
          <div><dt>Antigüedad</dt><dd>${valueOrDash(property.age_years, " años")}</dd></div>
        </dl>
        <p class="condition">${property.condition_label}</p>
        <p class="description">${property.description || "Sin descripción disponible."}</p>
        <a class="button secondary" href="${property.url}" target="_blank" rel="noopener noreferrer">Ver publicación</a>
      </div>
    </article>
  `;
}

function render() {
  const container = document.querySelector("#results");
  if (!state.filtered.length) {
    container.innerHTML = `<div class="empty"><h2>No hay resultados</h2><p>Revisá las fuentes y filtros en <code>config/searches.json</code>, y luego ejecutá el workflow.</p></div>`;
    return;
  }
  container.innerHTML = state.filtered.map(card).join("");
}

function applyControls() {
  const query = document.querySelector("#text-filter").value.toLowerCase().trim();
  const sort = document.querySelector("#sort").value;
  state.filtered = state.properties.filter((item) =>
    `${item.title} ${item.location} ${item.source} ${item.description}`.toLowerCase().includes(query)
  );

  state.filtered.sort((a, b) => {
    if (sort === "price-asc") return (a.price ?? Infinity) - (b.price ?? Infinity);
    if (sort === "price-desc") return (b.price ?? -1) - (a.price ?? -1);
    if (sort === "recent") return new Date(b.found_at) - new Date(a.found_at);
    return b.condition_score - a.condition_score;
  });
  render();
}

async function load() {
  try {
    const response = await fetch("../data/results.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.properties = payload.properties || [];
    state.filtered = [...state.properties];
    renderSummary(payload);
    const generated = payload.generated_at
      ? new Date(payload.generated_at).toLocaleString("es-AR")
      : "Todavía no se ejecutó una búsqueda";
    document.querySelector("#status").textContent = `Última actualización: ${generated}`;
    applyControls();
  } catch (error) {
    document.querySelector("#status").textContent = "No se pudieron cargar los resultados.";
    document.querySelector("#results").innerHTML = `<div class="empty"><p>${error.message}</p></div>`;
  }
}

document.querySelector("#text-filter").addEventListener("input", applyControls);
document.querySelector("#sort").addEventListener("change", applyControls);
load();
