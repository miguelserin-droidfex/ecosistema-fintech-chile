#!/usr/bin/env python3
"""
Actualiza automáticamente los scores del radar fintech Chile
usando Claude con web search. Se ejecuta via GitHub Actions el día 15.
"""
import json, os, re
from pathlib import Path
import anthropic
from datetime import datetime

# Cargar scores actuales
scores_path = Path("scores.json")
data = json.loads(scores_path.read_text())
rastreos = data["rastreos"]
current = rastreos[-1]["scores"] if rastreos else {}
empresas_list = "\n".join([f"{n} (actual: {s})" for n, s in current.items()])

# Label del nuevo rastreo
now = datetime.now()
meses = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
label = f"{meses[now.month-1]} {now.year}"
fecha = now.strftime("%Y-%m")

print(f"Actualizando radar para: {label}")
print(f"Empresas a evaluar: {len(current)}")

# Llamada a Claude con web search
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM = """Eres un analista del ecosistema fintech chileno. Busca noticias recientes
(últimas 4 semanas) de empresas fintech en Chile y evalúa su nivel de ruido mediático.

ESCALA 1-10:
- 9-10: cobertura masiva, funding grande, lanzamiento muy relevante
- 7-8: noticias importantes, actividad alta en prensa/redes  
- 5-6: actividad moderada, algunas noticias
- 3-4: actividad baja, poca presencia mediática
- 1-2: sin actividad mediática verificable

Responde ÚNICAMENTE con un JSON. Sin texto, sin markdown.
Solo incluye empresas donde el score CAMBIA respecto al actual.
Si no hay cambios responde exactamente: {}"""

USER = f"""Busca noticias y actividad reciente de estas empresas fintech chilenas
y devuelve los scores actualizados para {label}.

EMPRESAS:
{empresas_list}

Fuentes a revisar: FinteChile, Chile Fintech Forum, Diario Financiero,
El Mercurio Inversiones, LinkedIn, Instagram, App Store.

Responde SOLO con el JSON de scores que cambian."""

print("Consultando Claude con web search...")
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    tools=[{"type": "web_search_20250305", "name": "web_search"}],
    system=SYSTEM,
    messages=[{"role": "user", "content": USER}]
)

# Extraer texto de la respuesta (maneja tool_use automáticamente)
text = ""
for block in response.content:
    if hasattr(block, "text"):
        text += block.text

print(f"Respuesta recibida. Texto: {text[:200]}")

# Parsear JSON
clean = re.sub(r"```json|```", "", text).strip()
match = re.search(r"\{[\s\S]*?\}", clean)
if not match:
    print("No se encontraron cambios de score. Terminando sin modificar.")
    exit(0)

new_scores = json.loads(match.group(0))
changed = len(new_scores)

if changed == 0:
    print("Sin cambios detectados.")
    exit(0)

print(f"Cambios detectados ({changed} empresa(s)):")
for emp, sc in new_scores.items():
    prev = current.get(emp, 2)
    arrow = "UP" if sc > prev else "DOWN" if sc < prev else "="
    print(f"  {arrow} {emp}: {prev} -> {sc}")

# Actualizar scores.json
merged = {**current, **new_scores}

# Verificar si ya existe un rastreo de este mes
existing = next((r for r in rastreos if r["fecha"] == fecha), None)
if existing:
    existing["scores"] = merged
    print(f"Rastreo {label} actualizado (pisado).")
else:
    rastreos.append({"fecha": fecha, "label": label, "scores": merged})
    print(f"Nuevo rastreo {label} agregado.")

scores_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
print(f"scores.json guardado con {len(rastreos)} rastreos totales.")
