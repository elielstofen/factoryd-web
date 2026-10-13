#!/usr/bin/env python3
"""
Consigue la foto principal de un anuncio de Wallapop sin entrar en Wallapop
desde GitHub (que está bloqueado). Prueba, por orden, servicios que generan
la vista previa de un enlace (como WhatsApp al pegar un link):

  1. Microlink  (api.microlink.io, gratis hasta ~50 consultas al día)
  2. Jina Reader (r.jina.ai, gratis con límite de uso)

Solo acepta una URL de cdn.wallapop.com que pertenezca a ese anuncio.

Uso de prueba:
    python scripts/main_photo.py https://es.wallapop.com/item/...-1311160358
"""
import json
import re
import sys
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (compatible; factoryd-catalog/1.0)"}


def _get(url, accept="application/json", timeout=40):
    req = urllib.request.Request(url, headers={**UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _item_id(item_url):
    m = re.search(r"-(\d+)/?$", item_url.split("?")[0])
    return m.group(1) if m else None


def _pick(text, pid):
    """Primera URL de foto de Wallapop que sea de este anuncio."""
    for u in re.findall(r"https://cdn\.wallapop\.com/images/[^\s\"')\]>]+", text or ""):
        if pid in u:
            base = u.split("?")[0]
            return base + "?pictureSize=W800"
    return None


def via_microlink(item_url, pid):
    api = "https://api.microlink.io/?" + urllib.parse.urlencode({"url": item_url})
    data = json.loads(_get(api))
    img = ((data.get("data") or {}).get("image") or {}).get("url")
    return _pick(img, pid) if img else None


def via_jina(item_url, pid):
    return _pick(_get("https://r.jina.ai/" + item_url, accept="text/plain", timeout=60), pid)


def main_photo(item_url, log=print):
    pid = _item_id(item_url)
    if not pid:
        return None
    for name, fn in (("microlink", via_microlink), ("jina", via_jina)):
        try:
            url = fn(item_url, pid)
            log(f"  foto vía {name}: {'OK' if url else 'sin foto'} · {item_url}")
            if url:
                return url
        except Exception as e:
            log(f"  foto vía {name}: error {e} · {item_url}")
    return None


if __name__ == "__main__":
    for u in sys.argv[1:]:
        print(u, "->", main_photo(u))
