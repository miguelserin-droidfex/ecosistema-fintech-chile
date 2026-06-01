#!/usr/bin/env python3
"""
Actualiza el AFEX RADAR — Ecosistema Fintech Chile.

Estrategia (100% gratuita, sin API de pago):
  1. Google News RSS  → busca menciones por empresa (últimos 30 días)
  2. Cuenta artículos encontrados → deriva score de ruido mediático
  3. Aplica peso editorial según fuente (DF, Pulso, etc.)
  4. Actualiza scores.json con el nuevo rastreo mensual
  5. Inyecta el historial en index.html (reemplaza el bloque BASE_HIST)
  6. GitHub Actions hace commit + push automático

Sin Playwright, sin Railway, sin tarjeta.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

SCORES_PATH   = Path("scores.json")
HTML_PATH     = Path("index.html")
MESES_ES      = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                 "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# Fuentes con peso editorial (cuanto más alto, más vale un artículo de esa fuente)
FUENTES_PESO = {
    "diariofinanciero": 3.0, "df.cl": 3.0,
    "pulso.cl": 2.5, "elmercurio": 2.0,
    "emol.com": 1.8, "latercera": 1.8,
    "biobiochile": 1.5, "cnnchile": 1.5,
    "fintechile": 2.5, "chinafintech": 2.0,
    "linkedin": 1.2, "techcrunch": 2.0,
    "bloomberg": 3.0, "reuters": 3.0,
}

# Términos de contexto para refinar búsquedas (se agrega al query)
CONTEXTO = "fintech Chile"

# Ventana de búsqueda (días hacia atrás)
WINDOW_DAYS = 35

# Pausa entre requests (segundos) — para no saturar Google
DELAY_BETWEEN = 1.5

# User-Agent neutro
UA = "Mozilla/5.0 (compatible; RadarFintechBot/1.0)"

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def fetch_url(url: str, timeout: int = 12) -> bytes | None:
    """Descarga una URL con reintentos."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if attempt == 2:
                print(f"    ⚠ fetch failed ({url[:60]}): {e}")
            time.sleep(2)
    return None


def parse_rss_date(date_str: str) -> datetime | None:
    """Parsea fecha RFC-2822 de RSS."""
    try:
        # Ejemplo: "Mon, 15 May 2026 10:00:00 +0000"
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def peso_fuente(url: str) -> float:
    """Retorna el peso editorial según el dominio de la URL."""
    url_lower = url.lower()
    for k, v in FUENTES_PESO.items():
        if k in url_lower:
            return v
    return 1.0


def buscar_menciones(empresa: str) -> tuple[int, float]:
    """
    Busca menciones de la empresa en Google News RSS (últimos WINDOW_DAYS días).
    Retorna (n_articulos, score_ponderado).
    """
    query = f'"{empresa}" {CONTEXTO}'
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=es-419&gl=CL&ceid=CL:es-419"

    raw = fetch_url(url)
    if not raw:
        return 0, 0.0

    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    n_articulos = 0
    score_ponderado = 0.0

    try:
        root = ET.fromstring(raw)
        items = root.findall(".//item")
        for item in items:
            pub_date_el = item.find("pubDate")
            if pub_date_el is None or not pub_date_el.text:
                continue
            pub_date = parse_rss_date(pub_date_el.text)
            if pub_date is None or pub_date < cutoff:
                continue

            link_el = item.find("link")
            link = link_el.text if link_el is not None else ""
            peso = peso_fuente(link)

            n_articulos += 1
            # Decaimiento temporal: artículos más recientes valen más
            days_old = (datetime.now(timezone.utc) - pub_date).days
            decay = 1.0 - (days_old / WINDOW_DAYS) * 0.4  # entre 0.6 y 1.0
            score_ponderado += peso * decay

    except ET.ParseError as e:
        print(f"    ⚠ XML parse error para '{empresa}': {e}")

    return n_articulos, round(score_ponderado, 3)


def menciones_a_score(n: int, pond: float, score_anterior: int) -> int:
    """
    Convierte menciones + score ponderado → score 1-10 (entero).

    Tabla de calibración aproximada:
      pond >= 12  → 9-10  (cobertura masiva)
      pond 7-11   → 7-8   (noticias importantes)
      pond 3-6    → 5-6   (actividad moderada)
      pond 1-2    → 4     (actividad baja)
      pond 0      → mantiene score anterior (sin evidencia = sin cambio)

    Regla de estabilidad: máximo ±2 puntos por mes.
    """
    if pond == 0:
        return score_anterior  # Sin noticias = sin cambio

    if pond >= 12:
        raw = 9 if pond < 20 else 10
    elif pond >= 7:
        raw = 8 if pond >= 9 else 7
    elif pond >= 3:
        raw = 6 if pond >= 5 else 5
    elif pond >= 1:
        raw = 4
    else:
        raw = score_anterior

    # Estabilidad: no mover más de ±2 por mes
    delta = raw - score_anterior
    if delta > 2:
        raw = score_anterior + 2
    elif delta < -2:
        raw = score_anterior - 2

    return max(1, min(10, raw))


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    label = f"{MESES_ES[now.month - 1]} {now.year}"
    fecha = now.strftime("%Y-%m")

    print(f"╔══════════════════════════════════════════════════╗")
    print(f"  AFEX RADAR — Actualización {label}")
    print(f"  Método: Google News RSS (sin API de pago)")
    print(f"╚══════════════════════════════════════════════════╝\n")

    # 1. Cargar historial
    if not SCORES_PATH.exists():
        print("✗ scores.json no encontrado")
        sys.exit(1)

    data = json.loads(SCORES_PATH.read_text(encoding="utf-8"))
    rastreos = data["rastreos"]
    current = rastreos[-1]["scores"] if rastreos else {}
    empresas = list(current.keys())

    print(f"📊 {len(rastreos)} rastreos existentes · {len(empresas)} empresas a evaluar\n")

    # 2. Buscar menciones para cada empresa
    nuevos_scores = {}
    cambios = []

    for i, empresa in enumerate(empresas, 1):
        print(f"[{i:>3}/{len(empresas)}] {empresa:<35}", end="", flush=True)
        n, pond = buscar_menciones(empresa)
        anterior = current[empresa]
        nuevo = menciones_a_score(n, pond, int(round(anterior)))
        nuevos_scores[empresa] = nuevo

        if nuevo != int(round(anterior)):
            arrow = "↑" if nuevo > anterior else "↓"
            cambios.append({"empresa": empresa, "anterior": int(round(anterior)),
                            "nuevo": nuevo, "articulos": n, "pond": pond})
            print(f"  {n:>2} arts  pond={pond:>5.1f}  {int(round(anterior))} {arrow} {nuevo}")
        else:
            print(f"  {n:>2} arts  pond={pond:>5.1f}  = {nuevo}")

        time.sleep(DELAY_BETWEEN)

    # 3. Resumen de cambios
    print(f"\n{'─'*55}")
    print(f"✅ {len(cambios)} cambio(s) detectado(s)")
    for c in cambios:
        arrow = "↑" if c["nuevo"] > c["anterior"] else "↓"
        print(f"   {arrow}  {c['empresa']}: {c['anterior']} → {c['nuevo']}  ({c['articulos']} artículos)")

    # 4. Actualizar scores.json
    existing = next((r for r in rastreos if r["fecha"] == fecha), None)
    if existing:
        existing["scores"] = nuevos_scores
        print(f"\n📝 Rastreo '{label}' actualizado (pisado).")
    else:
        rastreos.append({"fecha": fecha, "label": label, "scores": nuevos_scores})
        print(f"\n📝 Nuevo rastreo '{label}' agregado. Total: {len(rastreos)} rastreos.")

    SCORES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"💾 scores.json guardado.")

    # 5. Inyectar en index.html
    inyectar_en_html(data)

    print("\n🏁 Actualización completada.")


def inyectar_en_html(data: dict):
    """
    Reemplaza el bloque BASE_HIST en index.html con los datos actualizados
    de scores.json. Busca el patrón 'const BASE_HIST=[...];' y lo reemplaza.
    """
    if not HTML_PATH.exists():
        print("⚠ index.html no encontrado — omitiendo inyección.")
        return

    html = HTML_PATH.read_text(encoding="utf-8")

    # Construir el nuevo objeto JS
    rastreos_js = []
    for r in data["rastreos"]:
        scores_js = json.dumps(r["scores"], ensure_ascii=False)
        rastreos_js.append(
            f'  {{fecha:\'{r["fecha"]}\','
            f'label:\'{r["label"]}\','
            f'scores:{scores_js}}}'
        )

    new_base_hist = "const BASE_HIST=[\n" + ",\n".join(rastreos_js) + "\n];"

    # Patrón a reemplazar: desde 'const BASE_HIST=[' hasta '];'
    pattern = re.compile(r"const BASE_HIST=\[[\s\S]*?\];", re.MULTILINE)
    match = pattern.search(html)

    if not match:
        print("⚠ No se encontró 'const BASE_HIST=[...]' en index.html — omitiendo inyección.")
        return

    new_html = html[:match.start()] + new_base_hist + html[match.end():]

    # Actualizar también el título y última actualización
    last_label = data["rastreos"][-1]["label"]
    new_html = re.sub(
        r"Ecosistema Financiero Chile · [A-Za-záéíóúÁÉÍÓÚñÑ]+ \d{4}",
        f"Ecosistema Financiero Chile · {last_label}",
        new_html
    )
    new_html = re.sub(
        r"Verificado &middot; [A-Za-záéíóúÁÉÍÓÚñÑ]+ \d{4} &middot;",
        f"Verificado &middot; {last_label} &middot;",
        new_html
    )
    new_html = re.sub(
        r"&Uacute;ltima actualizaci&oacute;n: [A-Za-záéíóúÁÉÍÓÚñÑ]+ \d{4}",
        f"&Uacute;ltima actualizaci&oacute;n: {last_label}",
        new_html
    )

    HTML_PATH.write_text(new_html, encoding="utf-8")
    n_rastreos = len(data["rastreos"])
    print(f"📄 index.html actualizado con {n_rastreos} rastreo(s) · última actualización: {last_label}")


if __name__ == "__main__":
    main()
