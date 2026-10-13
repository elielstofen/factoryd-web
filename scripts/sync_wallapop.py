#!/usr/bin/env python3
"""
Sincroniza el catálogo de un perfil de Wallapop en products.json.

Uso:
    python scripts/sync_wallapop.py

Configuración (variables de entorno, opcionales):
    WALLAPOP_PROFILE_URL  URL pública del perfil (por defecto, Factory D)
    WALLAPOP_USER_ID      ID interno del usuario en la API, si ya se conoce
    CATEGORY_MAP          Ruta a un JSON {"palabra clave": "Categoría"} para agrupar productos

Notas:
- Wallapop no tiene API pública oficial. El script usa los mismos endpoints
  que su web. Si cambian, hay que ajustar parse_item() / fetch_items().
- Si la descarga falla, el script NO sobrescribe products.json: la web sigue
  mostrando el último catálogo bueno.
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "products.json"

PROFILE_URL = os.environ.get("WALLAPOP_PROFILE_URL", "https://es.wallapop.com/user/factoryd-474056108")
USER_ID = os.environ.get("WALLAPOP_USER_ID", "").strip()
API = "https://api.wallapop.com/api/v3"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "X-DeviceOS": "0",
    "Origin": "https://es.wallapop.com",
    "Referer": "https://es.wallapop.com/",
}


def get(url, accept_json=True):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if accept_json else body


def resolve_user_id():
    """Saca el ID interno del usuario a partir del HTML del perfil."""
    if USER_ID:
        return USER_ID
    html = get(PROFILE_URL, accept_json=False)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        data = json.loads(m.group(1))
        # Buscamos el primer campo "id" dentro de un objeto de usuario
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "micro_name" in node and isinstance(node.get("id"), str):
                    return node["id"]
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    m = re.search(r'"userId"\s*:\s*"([a-z0-9]{8,})"', html)
    if m:
        return m.group(1)
    raise RuntimeError("No se encontró el ID interno del usuario. Defínelo en WALLAPOP_USER_ID.")


def fetch_user(uid):
    try:
        return get(f"{API}/users/{uid}")
    except Exception:
        return {}


def fetch_items(uid):
    items, url = [], f"{API}/users/{uid}/items"
    for _ in range(30):  # tope de páginas por seguridad
        page = get(url)
        items.extend(page.get("data", []))
        nxt = (page.get("meta") or {}).get("next")
        if not nxt:
            break
        url = f"{API}/users/{uid}/items?next={nxt}"
        time.sleep(1)
    return items


def load_category_map():
    path = os.environ.get("CATEGORY_MAP") or str(ROOT / "category_map.json")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def guess_category(item, cmap):
    title = (item.get("title") or "").lower()
    for kw, cat in cmap.items():
        if kw.lower() in title:
            return cat
    tax = item.get("taxonomy") or item.get("category") or []
    if isinstance(tax, list) and tax:
        return tax[0].get("name") if isinstance(tax[0], dict) else str(tax[0])
    if isinstance(tax, dict):
        return tax.get("name")
    return "Otros"


def enrich(item, description):
    """Saca de la descripción los datos que la web muestra en cada ficha."""
    desc = (description or "").lower()
    m = re.search(r"garant[ií]a(?:\s+de)?\s*:?\s*(\d+)\s*años?", desc) or \
        re.search(r"(\d+)\s*años?\s+de\s+garant[ií]a(?!\s+en)", desc)
    item["warranty"] = f"{m.group(1)} año{'s' if m.group(1) != '1' else ''}" if m else None
    item["condition"] = "Nuevo con tara" if re.search(r"nuev[oa]\s+con\s+(tara|defecto)", desc) else None
    text = (item.get("title") or "") + " " + (description or "")
    m = re.search(r"(\d{2,3})\s*[x×]\s*(\d{2,3})(?:\s*[x×]\s*(\d{2,3}))?\s*cm", text, re.I)
    item["size"] = " × ".join(g for g in m.groups() if g) + " cm" if m else None
    return item


def parse_item(raw, cmap):
    price = raw.get("price") or {}
    if isinstance(price, dict):
        amount, currency = price.get("amount"), price.get("currency", "EUR")
    else:
        amount, currency = price, raw.get("currency", "EUR")
    images = []
    for im in (raw.get("images") or [])[:6]:
        if isinstance(im, str):
            images.append(im)
            continue
        urls = im.get("urls") or {}
        u = urls.get("medium") or urls.get("big") or urls.get("small") or im.get("url") or im.get("original")
        if u:
            images.append(u)
    image = images[0] if images else None
    slug = raw.get("web_slug") or raw.get("slug") or raw.get("id")
    reserved = raw.get("reserved")
    if isinstance(reserved, dict):
        reserved = reserved.get("flag")
    shipping = raw.get("shipping") or {}
    description = raw.get("description") or ""
    return enrich({
        "id": raw.get("id"),
        "title": (raw.get("title") or "").strip(),
        "price": amount,
        "currency": currency,
        "category": guess_category(raw, cmap),
        "image": image,
        "images": images,
        "url": f"https://es.wallapop.com/item/{slug}",
        "reserved": bool(reserved),
        "shipping": bool(shipping.get("user_allows_shipping") if isinstance(shipping, dict) else shipping),
        "created_at": raw.get("created_at") or raw.get("modified_date"),
    }, description)


def main():
    previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    seller = dict(previous.get("seller") or {})
    try:
        uid = resolve_user_id()
        user = fetch_user(uid)
        raw = fetch_items(uid)
    except Exception as e:
        print(f"Error al sincronizar, se mantiene el catálogo anterior: {e}", file=sys.stderr)
        sys.exit(1)

    cmap = load_category_map()
    items = [parse_item(r, cmap) for r in raw]
    items = [i for i in items if i["title"] and not i.get("sold")]
    items.sort(key=lambda i: i.get("created_at") or 0, reverse=True)

    if not items and previous.get("items"):
        print("La API devolvió 0 productos; no se sobrescribe por precaución.", file=sys.stderr)
        sys.exit(1)

    loc = (user.get("location") or {}) if isinstance(user, dict) else {}
    if loc.get("city"):
        seller["location"] = loc["city"]
    seller.setdefault("wallapop_url", PROFILE_URL)

    out = {
        "example": False,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seller": seller,
        "items": items,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(items)} productos guardados en {OUT.name}")


if __name__ == "__main__":
    main()
