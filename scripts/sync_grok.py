#!/usr/bin/env python3
"""
Sincroniza el catálogo pidiéndoselo a Grok (API de xAI), que navega Wallapop
con su buscador web y devuelve los productos en JSON.

Necesita la variable de entorno XAI_API_KEY (en GitHub: Settings → Secrets → Actions).
Opcional: XAI_MODEL (por defecto grok-4.7).

Antes de guardar, valida lo que devuelve Grok para no publicar datos inventados:
- cada producto debe tener un enlace real de anuncio (es.wallapop.com/item/...-<id>)
- las fotos deben ser de cdn.wallapop.com y pertenecer a ese anuncio
- si el número de productos cae a menos de la mitad del catálogo anterior, no se guarda
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_wallapop import OUT, PROFILE_URL, enrich, guess_category, load_category_map  # noqa: E402

API_URL = "https://api.x.ai/v1/responses"
MODEL = os.environ.get("XAI_MODEL", "grok-4.7")
DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "debug"))

PROMPT = f"""Navega este perfil de Wallapop y extrae su catálogo completo: {PROFILE_URL}

1. Abre el perfil y carga TODOS los productos en venta (haz scroll / "Ver más" hasta el final).
2. Entra en cada anuncio para sacar: título, precio, categoría, descripción completa, si está reservado, si tiene envío y las URLs de TODAS sus fotos.
3. Del vendedor saca: ciudad, número de valoraciones y número de ventas.

REGLAS
- No inventes nada. Si un dato no aparece, pon null.
- Las fotos tienen que ser URLs reales de https://cdn.wallapop.com/images/... completas.
- El enlace de cada producto es el del anuncio: https://es.wallapop.com/item/...
- No incluyas productos vendidos.
- Si no puedes acceder a Wallapop, responde exactamente {{"error": "motivo"}}.

Responde SOLO con un JSON válido, sin texto antes ni después, con esta forma:
{{"seller": {{"location": "...", "reviews": 0, "sales": 0}},
  "items": [{{"title": "...", "price": 0, "currency": "EUR", "category": "...", "description": "...",
             "images": ["https://cdn.wallapop.com/images/..."], "url": "https://es.wallapop.com/item/...",
             "reserved": false, "shipping": false}}]}}
"""


def ask_grok(key):
    body = {
        "model": MODEL,
        "input": [{"role": "user", "content": PROMPT}],
        "tools": [{"type": "web_search", "enable_image_understanding": False}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode("utf-8"))


def output_text(resp):
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"]
    parts = []
    for item in resp.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(c["text"])
    return "\n".join(parts)


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("La respuesta no contiene JSON")
    return json.loads(text[start:end + 1])


def clean_items(raw_items, cmap):
    items, rejected = [], []
    for r in raw_items or []:
        url = (r.get("url") or "").split("?")[0].replace("://www.wallapop.com", "://es.wallapop.com")
        m = re.match(r"^https://es\.wallapop\.com/item/[a-z0-9-]+-(\d+)$", url)
        title = " ".join((r.get("title") or "").split())
        if not m or not title:
            rejected.append(title or url or "?")
            continue
        pid = m.group(1)
        imgs = [u for u in (r.get("images") or []) if isinstance(u, str)
                and u.startswith("https://cdn.wallapop.com/images/") and pid in u][:6]
        try:
            price = float(str(r.get("price")).replace(",", "."))
        except (TypeError, ValueError):
            rejected.append(title)
            continue
        it = {
            "id": url.rsplit("/", 1)[-1],
            "title": title,
            "price": price,
            "currency": r.get("currency") or "EUR",
            "category": guess_category({"title": title, "category": [r.get("category") or "Otros"]}, cmap),
            "image": imgs[0] if imgs else None,
            "images": imgs,
            "url": url,
            "reserved": r.get("reserved") if isinstance(r.get("reserved"), bool) else None,
            "shipping": r.get("shipping") if isinstance(r.get("shipping"), bool) else None,
        }
        items.append(enrich(it, r.get("description")))
    return items, rejected


def merge_previous(items, previous_items):
    """Rellena lo que Grok no trae (fotos, reservado, garantía...) con lo último conocido de ese anuncio."""
    prev = {p.get("url"): p for p in previous_items or []}
    for it in items:
        p = prev.get(it["url"], {})
        if not it["images"] and p.get("images"):
            it["images"], it["image"] = p["images"], p.get("image") or p["images"][0]
        for k in ("reserved", "shipping"):
            if it[k] is None:
                it[k] = bool(p.get(k))
        for k in ("warranty", "condition", "size"):
            if not it.get(k) and p.get(k):
                it[k] = p[k]
    return items


def main():
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        print("Falta XAI_API_KEY.", file=sys.stderr)
        sys.exit(1)
    DEBUG_DIR.mkdir(exist_ok=True)
    previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}

    try:
        resp = ask_grok(key)
    except Exception as e:
        detail = e.read().decode("utf-8", "replace")[:2000] if hasattr(e, "read") else ""
        print(f"Error llamando a Grok: {e} {detail}", file=sys.stderr)
        sys.exit(1)
    (DEBUG_DIR / "grok_response.json").write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Uso:", json.dumps(resp.get("usage", {}), ensure_ascii=False))

    try:
        data = extract_json(output_text(resp))
    except Exception as e:
        print(f"No se pudo leer el JSON de Grok: {e}", file=sys.stderr)
        sys.exit(1)
    if data.get("error"):
        print(f"Grok no pudo acceder: {data['error']}", file=sys.stderr)
        sys.exit(1)

    items, rejected = clean_items(data.get("items"), load_category_map())
    items = merge_previous(items, previous.get("items"))
    print(f"Grok devolvió {len(data.get('items') or [])} productos; válidos: {len(items)}; descartados: {len(rejected)}")
    if rejected:
        print("Descartados:", "; ".join(rejected[:20]))

    prev_n = len(previous.get("items") or [])
    if not items or (prev_n and len(items) < prev_n * 0.5):
        print(f"Demasiados pocos productos válidos ({len(items)} frente a {prev_n} antes). No se guarda.", file=sys.stderr)
        sys.exit(1)

    seller = dict(previous.get("seller") or {})
    s = data.get("seller") or {}
    for k in ("reviews", "sales"):
        if isinstance(s.get(k), int):
            seller[k] = s[k]
    seller.setdefault("wallapop_url", PROFILE_URL)

    out = {
        "example": False,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seller": seller,
        "items": items,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(items)} productos guardados.")


if __name__ == "__main__":
    main()
