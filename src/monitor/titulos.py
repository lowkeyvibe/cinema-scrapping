"""Títulos de películas en inglés, vía TMDB (themoviedb.org).

    from src.monitor.titulos import en_ingles
    en_ingles("DUNA: PARTE TRES", 2026)   # -> "Dune: Part Three"

Busca el título en castellano (con el año, si se sabe) y devuelve el título en
inglés. Los resultados se guardan en state/titulos.json para no repetir la
consulta en cada corrida. Ese archivo se puede editar a mano para corregir un
título mal encontrado: lo que esté ahí manda.

Si no hay TMDB_TOKEN, si TMDB no la encuentra o si falla la consulta, devuelve
el título original, así un aviso nunca sale sin nombre.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

CACHE = Path("state/titulos.json")
API = "https://api.themoviedb.org/3/search/movie"
REINTENTAR_DESPUES = 3 * 24 * 3600  # una película no encontrada se vuelve a buscar a los 3 días

_cache: dict | None = None
_cambios = False


def en_ingles(titulo: str, anio: int | str | None = None) -> str:
    """El título en inglés, o `titulo` tal cual si no se puede averiguar."""
    if not titulo:
        return titulo
    clave = _clave(titulo)
    cache = _cargar()
    guardado = cache.get(clave)
    if guardado and (guardado.get("en") or time.time() - guardado.get("t", 0) < REINTENTAR_DESPUES):
        return guardado.get("en") or titulo

    token = os.environ.get("TMDB_TOKEN")
    if not token:
        return titulo
    try:
        en = _buscar(titulo, anio, token)
    except Exception as e:  # noqa: BLE001 - sin TMDB el aviso sale igual, en castellano
        print(f"  ! TMDB ({titulo}): {e}")
        return titulo
    _guardar(clave, {"es": titulo, "en": en, "t": int(time.time())})
    return en or titulo


def guardar_cache() -> None:
    """Escribe state/titulos.json si se agregaron títulos nuevos."""
    global _cambios
    if _cambios and _cache is not None:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        _cambios = False


# ---------------------------------------------------------------------------

def _buscar(titulo: str, anio, token: str) -> str | None:
    headers = {"Authorization": f"Bearer {token}"}
    consulta = re.sub(r"\s+", " ", titulo).strip()
    # Primero con el año (y el anterior, por estrenos de fin de año), después sin año.
    intentos = [int(anio), int(anio) - 1, None] if anio else [None]
    for a in intentos:
        params = {"query": consulta, "language": "en-US", "region": "AR", "include_adult": "false"}
        if a:
            params["primary_release_year"] = a
        r = requests.get(API, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        resultados = r.json().get("results") or []
        if resultados:
            return _prolijo(resultados[0].get("title") or resultados[0].get("original_title"))
    return None


def _prolijo(t: str | None) -> str | None:
    """TMDB a veces tiene títulos en MAYÚSCULAS: los paso a 'Title Case'."""
    if not t or t != t.upper() or not re.search(r"[A-Z]", t):
        return t
    chicas = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
    palabras = t.lower().split(" ")
    salida = []
    for i, p in enumerate(palabras):
        anterior = salida[-1] if salida else ""
        if i and p in chicas and not anterior.endswith(":"):
            salida.append(p)
        else:
            salida.append("-".join(s[:1].upper() + s[1:] for s in p.split("-")))
    return " ".join(salida)


def _clave(titulo: str) -> str:
    return re.sub(r"\s+", " ", titulo).strip().lower()


def _cargar() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _guardar(clave: str, valor: dict) -> None:
    global _cambios
    _cargar()[clave] = valor
    _cambios = True
