"""Scraper de Cinemark Argentina.

El sitio (www.cinemark.com.ar) carga todo desde una API JSON en
bff.cinemark.com.ar, así que la consultamos directamente en lugar de
renderizar el HTML. La API exige el encabezado `country: AR`.
"""

from __future__ import annotations

import time
from datetime import datetime

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

CADENA = "Cinemark"
API = "https://bff.cinemark.com.ar/api/cinema"
SITE = "https://www.cinemark.com.ar"
HEADERS = {
    "country": "AR",
    "Accept": "application/json",
    "Origin": SITE,
    "Referer": SITE + "/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
}
PAUSA_ENTRE_CINES = 1.0  # segundos, para no saturar el sitio


class CinemarkScraper:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    def _get(self, path: str, **params) -> list[dict]:
        r = self.session.get(f"{API}/{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()["data"]

    def peliculas(self) -> list[dict]:
        rows = []
        for m in self._get("movies"):
            rows.append({
                "cadena": CADENA,
                "pelicula_id": m.get("corporateId"),
                "titulo": m.get("title"),
                "estado": {"SHOWING_NOW": "En cartelera", "PRESALE": "Preventa",
                           "COMING_SOON": "Próximamente"}.get(m.get("status"), m.get("status")),
                "estreno": (m.get("openingDate") or "")[:10],
                "duracion_min": m.get("runTime"),
                "calificacion": m.get("rating"),
                "formatos": ", ".join(f["name"] for f in m.get("formats", [])),
                "idiomas": ", ".join(l["name"] for l in m.get("languages", [])),
                "poster": m.get("posterUrl"),
                "url": f"{SITE}/pelicula/{m.get('slug')}",
            })
        return rows

    def cines(self) -> list[dict]:
        rows = []
        for t in self._get("theaters", limit=1000):
            rows.append({
                "cadena": CADENA,
                "cine_id": t["id"],
                "cine": t["name"],
                "direccion": t.get("address"),
                "ciudad": t.get("city"),
                "region": (t.get("location") or {}).get("name"),
                "salas": t.get("totalCinemaRooms"),
                "latitud": t.get("latitude"),
                "longitud": t.get("longitude"),
                "url": f"{SITE}/cartelera/{t['slug']}",
            })
        return rows

    def funciones(self, cines: list[dict], log=print) -> list[dict]:
        rows = []
        for i, cine in enumerate(cines, 1):
            data = self._get("showtimes", theater=cine["cine_id"])
            log(f"  [{i}/{len(cines)}] {cine['cine']}: {len(data)} funciones")
            for s in data:
                # La API marca la hora como UTC ("Z") pero en realidad es hora local.
                dt = s["sessionDateTime"]
                occ = s.get("occupation") or {}
                rows.append({
                    "cadena": CADENA,
                    "cine": cine["cine"],
                    "ciudad": cine["ciudad"],
                    "pelicula": s.get("movieName"),
                    "pelicula_id": s.get("corporateId"),
                    "fecha": s.get("sessionDisplayDate"),
                    "hora": dt[11:16],
                    "trasnoche": "Sí" if s.get("isLateNightSession") else "No",
                    "sala": s.get("theaterRoom"),
                    "formato": s.get("sessionFormat"),
                    "idioma": (s.get("language") or {}).get("name"),
                    "asientos_libres": occ.get("availableSeats"),
                    "capacidad": occ.get("capacity"),
                    # status describe cuántos asientos quedan (HIGH = muchos libres).
                    "disponibilidad": {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Baja", "FULL": "Agotada"}.get(
                        occ.get("status"), occ.get("status")),
                    "funcion_id": s.get("sessionId"),
                })
            time.sleep(PAUSA_ENTRE_CINES)
        return rows

    def scrapear(self, log=print) -> dict[str, list[dict]]:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
        log("Cinemark: descargando películas y cines…")
        peliculas, cines = self.peliculas(), self.cines()
        log(f"  {len(peliculas)} películas, {len(cines)} cines")
        log("Cinemark: descargando funciones…")
        funciones = self.funciones(cines, log=log)
        for tabla in (peliculas, cines, funciones):
            for r in tabla:
                r["actualizado"] = ahora
        return {"Peliculas": peliculas, "Cines": cines, "Funciones": funciones}
