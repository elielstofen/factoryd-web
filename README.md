# Factory D — escaparate web conectado a Wallapop

Web estática de una sola página: presenta la tienda y muestra el catálogo que hay en Wallapop. Cada producto enlaza a su anuncio, donde se hace la compra.

## Cómo funciona

```
Wallapop ──(2 veces al día)──► scripts/sync_wallapop.py ──► products.json ──► index.html
```

- `index.html` lee `products.json` y pinta el catálogo (filtros por categoría, búsqueda, orden por precio, reservados al final).
- `scripts/sync_wallapop_browser.py` abre el perfil con un navegador Chromium real (sin ventana), hace scroll, lee los productos y reescribe `products.json`. Es el método principal.
- `scripts/sync_wallapop.py` es el plan B sin navegador. Si cualquiera de los dos falla, no toca el archivo, así que la web nunca se queda vacía.
- `.github/workflows/sync.yml` ejecuta todo a las 08:00 y 20:00 y publica los cambios. En cada ejecución guarda una captura del perfil (pestaña *Artifacts* de la ejecución) para ver qué vio el navegador.

### Probarlo en tu ordenador

```
pip install playwright
python -m playwright install chromium
HEADLESS=0 python scripts/sync_wallapop_browser.py   # con ventana, para verlo navegar
```

## Puesta en marcha (≈15 min)

1. Crear un repositorio en GitHub y subir esta carpeta.
2. Settings → Pages → desplegar desde la rama `main` (o conectar el repo a Netlify / Vercel / Cloudflare Pages).
3. Actions → «Sincronizar catálogo de Wallapop» → *Run workflow* para la primera carga.
4. Apuntar el dominio (p. ej. `factoryd.es`) en la configuración de Pages.

## Personalizar

- Textos de la tienda (nombre, frase, quiénes somos, ubicación): bloque `seller` de `products.json`. El script los conserva en cada sincronización.
- Categorías propias: crear `category_map.json`, por ejemplo `{"lámpara": "Iluminación", "silla": "Muebles"}`.

## Pendiente de validar

El script usa los endpoints internos de la web de Wallapop, que no son públicos ni están documentados. Hay que probarlo una vez desde un ordenador o desde GitHub Actions y ajustar los campos si la respuesta ha cambiado. Conviene revisar también las condiciones de uso de Wallapop sobre acceso automatizado.
