"""Títulos de películas en inglés, vía TMDB (themoviedb.org).

    from src.monitor.titulos import en_ingles
    en_ingles("DUNA: PARTE TRES", 2026)   # -> "Dune: Part Three"

Busca el título en castellano (con el año, si se sabe) y devuelve el título en
inglés. Los resultados se guardan en state/titulos.json para no repetir la
consulta en cada corrida, junto con el póster (poster()). Ese archivo se puede
editar a mano para corregir un título mal encontrado: poné "manual": true en la
entrada y no se vuelve a pisar.

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
IMAGENES = "https://image.tmdb.org/t/p/w780"
REINTENTAR_DESPUES = 3 * 24 * 3600  # una película no encontrada se vuelve a buscar a los 3 días

_cache: dict | None = None
_cambios = False


def en_ingles(titulo: str, anio: int | str | None = None) -> str:
    """El título en inglés, o `titulo` tal cual si no se puede averiguar."""
    if not titulo:
        return titulo
    return (_datos(titulo, anio) or {}).get("en") or titulo


def poster(titulo: str, anio: int | str | None = None) -> str | None:
    """URL del póster en TMDB (arte en inglés si hay), o None."""
    if not titulo:
        return None
    return (_datos(titulo, anio) or {}).get("poster")


def _datos(titulo: str, anio) -> dict | None:
    """Lo que hay en el caché para esta película; si falta, lo busca en TMDB."""
    clave = _clave(titulo)
    guardado = _cargar().get(clave)
    completo = guardado and "poster" in guardado
    if completo and (guardado.get("en") or time.time() - guardado.get("t", 0) < REINTENTAR_DESPUES):
        return guardado

    token = os.environ.get("TMDB_TOKEN")
    if not token:
        return guardado
    try:
        en, img = _buscar(titulo, anio, token)
    except Exception as e:  # noqa: BLE001 - sin TMDB el aviso sale igual, en castellano
        print(f"  ! TMDB ({titulo}): {e}")
        return guardado
    nuevo = {"es": titulo, "en": en, "poster": img, "t": int(time.time())}
    if guardado and guardado.get("manual"):
        nuevo["en"] = guardado.get("en")   # un título corregido a mano no se pisa
        nuevo["manual"] = True
    _guardar(clave, nuevo)
    return nuevo


def guardar_cache() -> None:
    """Escribe state/titulos.json si se agregaron títulos nuevos."""
    global _cambios
    if _cambios and _cache is not None:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        _cambios = False


# ---------------------------------------------------------------------------

def _buscar(titulo: str, anio, token: str) -> tuple[str | None, str | None]:
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
            m = resultados[0]
            img = f"{IMAGENES}{m['poster_path']}" if m.get("poster_path") else None
            return _prolijo(m.get("title") or m.get("original_title")), img
    return None, None


def _prolijo(t: str | None) -> str | None:
    """TMDB a veces tiene títulos en MAYÚSCULAS: los paso a 'Title Case'."""
    if not t or t != t.upper() or not re.search(r"[A-Z]", t):
        return t
    chicas = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"}
    salida = []
    for i, original in enumerate(t.split(" ")):
        p = original.lower()
        anterior = salida[-1] if salida else ""
        if len(original) > 1 and not re.search(r"[AEIOUY0-9]", original) and re.search(r"[A-Z]", original):
            salida.append(original)          # siglas sin vocales (BTS, NCT): quedan en mayúscula
        elif i and p in chicas and not anterior.endswith(":"):
            salida.append(p)
        else:
            salida.append("-".join(_mayuscula_inicial(s) for s in p.split("-")))
    return " ".join(salida)


def _mayuscula_inicial(palabra: str) -> str:
    """Primera letra en mayúscula, salteando comillas o signos del principio ('arirang' -> 'Arirang')."""
    m = re.match(r"[^\w]*", palabra)          # signos del principio; si empieza con número ("1st"), no se toca
    i = m.end()
    return palabra[:i] + palabra[i:i + 1].upper() + palabra[i + 1:] if palabra[i:i + 1].isalpha() else palabra


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
