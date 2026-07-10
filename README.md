# Buscador de alquileres

Aplicación personal para buscar propiedades en alquiler en sitios de inmobiliarias, aplicar filtros configurables y generar un informe web legible.

## Qué hace

- Consulta las fuentes definidas en `config/searches.json`.
- Extrae publicaciones usando JSON-LD y, cuando hace falta, selectores CSS configurables.
- Normaliza precio, ambientes, superficie, cochera, antigüedad y estado.
- Descarta publicaciones que no cumplen los filtros.
- Penaliza textos asociados a propiedades deterioradas o antiguas.
- Elimina duplicados.
- Genera:
  - `data/results.json` para la web.
  - `data/results.csv` para Excel.
  - `docs/index.html` como informe visual.
- Puede ejecutarse manualmente o automáticamente desde GitHub Actions.

## Configurar búsquedas

Editar `config/searches.json` desde GitHub.

### Filtros principales

```json
{
  "filters": {
    "zones": ["Ramos Mejía", "Haedo"],
    "property_types": ["departamento", "ph"],
    "min_rooms": 2,
    "max_rooms": 4,
    "max_price": 900000,
    "max_expenses": 180000,
    "min_area_m2": 45,
    "parking_required": false,
    "max_age_years": 35,
    "exclude_condition_score_below": 45
  }
}
```

### Agregar una inmobiliaria

Cada sitio puede tener una estructura distinta. La forma más confiable es agregar sus selectores CSS:

```json
{
  "name": "Nombre de la inmobiliaria",
  "enabled": true,
  "start_urls": ["https://ejemplo.com/alquileres"],
  "selectors": {
    "card": ".property-card",
    "title": ".property-title",
    "url": "a",
    "price": ".property-price",
    "location": ".property-location",
    "description": ".property-description",
    "image": "img"
  }
}
```

Si el sitio publica información estructurada en JSON-LD, el scraper intenta leerla automáticamente aunque no se definan selectores.

## Ejecutar manualmente

1. Entrar a la pestaña **Actions**.
2. Abrir **Buscar alquileres**.
3. Presionar **Run workflow**.
4. Elegir si se quiere guardar los resultados en el repositorio.

## Ejecución automática

El workflow está configurado para ejecutarse una vez por día. La frecuencia puede cambiarse en `.github/workflows/search.yml`.

## Ver el informe

Activar GitHub Pages:

1. Ir a **Settings → Pages**.
2. En **Build and deployment**, elegir **Deploy from a branch**.
3. Seleccionar `main` y la carpeta `/docs`.

La dirección será:

`https://moralespablomp.github.io/alquiler/`

## Ejecución local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

En Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

## Criterio de estado

El sistema asigna un puntaje orientativo de 0 a 100 según palabras de la publicación:

- Mejora el puntaje: `reciclado`, `refaccionado`, `a estrenar`, `excelente estado`, `impecable`.
- Reduce el puntaje: `a refaccionar`, `estado original`, `de época`, `requiere mejoras`, `humedad`.

No reemplaza una visita. Sirve para ordenar y priorizar resultados.

## Uso responsable

El proyecto consulta solamente páginas públicas y no intenta evitar captchas, accesos restringidos ni medidas de seguridad. Conviene mantener una frecuencia baja y revisar los términos de cada sitio.