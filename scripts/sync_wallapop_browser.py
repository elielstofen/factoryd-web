#!/usr/bin/env python3
"""
Sincroniza el catálogo de Wallapop con un navegador real (Chromium sin ventana).

Abre el perfil como lo haría una persona, deja que la página cargue sus datos,
hace scroll hasta el final y guarda los productos en products.json.

Lee los productos de dos maneras:
  1. Captura las respuestas de datos que la propia página pide a Wallapop
     (lo más fiable: incluye precio, fotos, reservado, envío...).
  2. Si no captura nada, lee las tarjetas de producto que se ven en pantalla.

Instalación local:
    pip install playwright
    python -m playwright install chromium
    python scripts/sync_wallapop_browser.py

Si algo falla, NO sobrescribe products.json.
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_wallapop import OUT, PROFILE_URL, load_category_map, parse_item  # noqa: E402

from playwright.async_api import async_playwright

HEADLESS = os.environ.get("HEADLESS", "1") != "0"
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "debug"))
ITEMS_API = re.compile(r"api\.wallapop\.com/api/v3/(users/[^/]+/items|user/[^/]+/items|.*published)")
USER_API = re.compile(r"api\.wallapop\.com/api/v3/users/[^/?]+$")


async def accept_cookies(page):
    for label in ["Aceptar todo", "Aceptar", "Accept all", "Aceptar y cerrar"]:
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count():
                await btn.first.click(timeout=2000)
                return
        except Exception:
            pass


async def scroll_to_end(page, max_rounds=40):
    last = 0
    for _ in range(max_rounds):
        await page.mouse.wheel(0, 2500)
        await page.wait_for_timeout(900)
        # Algunos perfiles tienen botón "Ver más productos"
        for label in ["Ver más productos", "Cargar más", "Ver más"]:
            try:
                b = page.get_by_role("button", name=label)
                if await b.count() and await b.first.is_visible():
                    await b.first.click(timeout=2000)
                    await page.wait_for_timeout(1200)
            except Exception:
                pass
        h = await page.evaluate("document.body.scrollHeight")
        if h == last:
            break
        last = h


async def read_cards_from_dom(page):
    """Plan B: leer las tarjetas visibles."""
    return await page.evaluate(
        """() => {
          const out = [], seen = new Set();
          document.querySelectorAll('a[href*="/item/"]').forEach(a => {
            const href = a.href.split('?')[0];
            if (seen.has(href)) return; seen.add(href);
            const img = a.querySelector('img');
            const text = a.innerText || '';
            const m = text.match(/(\\d{1,3}(?:[.\\s]\\d{3})*(?:,\\d{1,2})?)\\s*€/);
            const price = m ? parseFloat(m[1].replace(/[.\\s]/g,'').replace(',','.')) : null;
            const title = (img && img.alt) || a.getAttribute('title') ||
                          text.split('\\n').map(s=>s.trim()).filter(s=>s && !/€/.test(s))[0] || '';
            out.push({
              id: href.split('/item/')[1],
              title: title.trim(),
              price,
              currency: 'EUR',
              image: img ? (img.currentSrc || img.src) : null,
              url: href,
              reserved: /reservado/i.test(text),
              shipping: /env[ií]o/i.test(text),
            });
          });
          return out;
        }"""
    )


async def run():
    captured, user_info = [], {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = await ctx.new_page()

        async def on_response(resp):
            url = resp.url
            try:
                if ITEMS_API.search(url) and resp.ok:
                    data = await resp.json()
                    rows = data.get("data") if isinstance(data, dict) else data
                    if isinstance(rows, list):
                        captured.extend(r for r in rows if isinstance(r, dict))
                elif USER_API.search(url) and resp.ok:
                    user_info.update(await resp.json())
            except Exception:
                pass

        page.on("response", on_response)

        await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        await accept_cookies(page)
        await page.wait_for_timeout(1500)
        await scroll_to_end(page)
        await page.wait_for_timeout(1500)

        dom_items = await read_cards_from_dom(page)

        DEBUG_DIR.mkdir(exist_ok=True)
        await page.screenshot(path=str(DEBUG_DIR / "perfil.png"), full_page=True)
        (DEBUG_DIR / "perfil.html").write_text(await page.content(), encoding="utf-8")
        await browser.close()

    cmap = load_category_map()
    items = []
    if captured:
        seen = set()
        for r in captured:
            it = parse_item(r, cmap)
            if it["id"] and it["id"] not in seen and it["title"]:
                seen.add(it["id"])
                items.append(it)
        source = "datos de la página"
    else:
        items = [dict(i, category="Otros") for i in dom_items if i["title"]]
        source = "tarjetas en pantalla"

    return items, user_info, source


def main():
    previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    try:
        items, user, source = asyncio.run(run())
    except Exception as e:
        print(f"Error al abrir Wallapop, se mantiene el catálogo anterior: {e}", file=sys.stderr)
        sys.exit(1)

    if not items:
        print("No se encontraron productos (posible bloqueo o cambio en la web). "
              "Revisa debug/perfil.png. No se sobrescribe el catálogo.", file=sys.stderr)
        sys.exit(1)

    seller = dict(previous.get("seller") or {})
    loc = user.get("location") or {}
    if isinstance(loc, dict) and loc.get("city"):
        seller["location"] = loc["city"]
    seller.setdefault("wallapop_url", PROFILE_URL)

    out = {
        "example": False,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seller": seller,
        "items": items,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(items)} productos guardados ({source}).")


if __name__ == "__main__":
    main()
