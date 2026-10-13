#!/usr/bin/env python3
"""
Importa un catálogo pegado desde la app de Grok (que sí trae fotos).

Uso: python scripts/import_grok.py imports/catalogo.json

- Acepta el JSON tal cual lo devuelve Grok (aunque venga con ```json o texto alrededor).
- Valida cada producto igual que la sincronización diaria (enlace real, fotos de ese anuncio).
- Sustituye el catálogo por el importado, conservando fotos/garantías conocidas si faltan.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_grok import OUT, PROFILE_URL, clean_items, extract_json, load_category_map, merge_previous  # noqa: E402


def main(path):
    data = extract_json(Path(path).read_text(encoding="utf-8"))
    previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    items, rejected = clean_items(data.get("items"), load_category_map())
    items = merge_previous(items, previous.get("items"))
    print(f"Importados: {len(items)} válidos, {len(rejected)} descartados; con foto: {sum(1 for i in items if i['images'])}")
    if rejected:
        print("Descartados:", "; ".join(rejected[:20]))
    if not items:
        sys.exit("No hay productos válidos en el archivo.")

    seller = dict(previous.get("seller") or {})
    s = data.get("seller") or {}
    for k in ("reviews", "sales"):
        if isinstance(s.get(k), int):
            seller[k] = s[k]
    seller.setdefault("wallapop_url", PROFILE_URL)

    OUT.write_text(json.dumps({
        "example": False,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seller": seller,
        "items": items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
