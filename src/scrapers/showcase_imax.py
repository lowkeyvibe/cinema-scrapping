"""Scraper de la cartelera IMAX de Showcase (IMAX Theatre, Norcenter).

Solo trae la sala IMAX, nada del resto de los cines Showcase.

- La lista de películas IMAX sale del bloque `#cartelera_cine_40219` de la
  home de www.todoshowcase.com (es lo que muestra el selector "IMAX THEATRE").
- Los horarios salen de la API JSON que usa la página de entradas:
  api.voyalcine.net/films/{filmid}/tree/{house_id}  (house_id 3250 = Showcase).
- Los datos de cada película (duración, calificación, etc.) se leen del HTML
  de su página en entradas.todoshowcase.com.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

CADENA = "Showcase"
SITE = "https://www.todoshowcase.com"
ENTRADAS = "https://entradas.todoshowcase.com/showcase"
API = "https://api.voyalcine.net/films/{film_id}/tree/{house_id}"
HOUSE_ID = 3250          # la cadena Showcase dentro de Voy al Cine
BLOQUE_IMAX = "cartelera_cine_40219"
CINE_IMAX_ID = 18        # id del IMAX Theatre dentro del árbol de la API
CINE = {
    "cine_id": CINE_IMAX_ID,
    "cine": "IMAX Theatre (Norcenter)",
    "direccion": "Panamericana y Debenedetti (B1605DT)",
    "ciudad": "Vicente López",
    "region": "Buenos Aires",
    "salas": 1,
    "url": f"{SITE}/cine/1/imax",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Referer": SITE + "/",
}
PAUSA = 1.0  # segundos entre pedidos, para no saturar el sitio


class ShowcaseImaxScraper:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    def _get(self, url: str) -> requests.Response:
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        return r

    def cartelera_imax(self) -> list[dict]:
        """Películas del bloque IMAX de la home: id, título, póster, etiquetas."""
        soup = BeautifulSoup(self._get(SITE + "/").text, "lxml")
        bloque = soup.find(id=BLOQUE_IMAX)
        if bloque is None:
            raise RuntimeError(f"No se encontró #{BLOQUE_IMAX} en la home de Showcase")
        pelis = []
        for box in bloque.select(".boxfilm"):
            link = box.select_one(".titulo-pelicula a")
            m = re.search(r"filmid=(\d+)", link["href"]) if link else None
            if not m:
                continue
            img = box.select_one(".afiche-pelicula img")
            tags = [p.get_text(strip=True) for p in box.select(".tag-container p")]
            pelis.append({
                "pelicula_id": int(m.group(1)),
                "titulo": link.get_text(strip=True),
                "poster": img["src"] if img else None,
                "anticipada": "ANTICIPADA" in tags,
            })
        return pelis

    def _detalle(self, film_id: int) -> dict:
        """Datos de la ficha (lista 'Título Original:', 'Duración:', ...)."""
        soup = BeautifulSoup(self._get(f"{ENTRADAS}/pelicula?filmid={film_id}&house_id={HOUSE_ID}").text, "lxml")
        datos = {}
        for li in soup.select("li"):
            s = li.find("strong")
            if s and s.get_text(strip=True).endswith(":"):
                clave = s.get_text(strip=True).rstrip(":").strip()
                datos[clave] = li.get_text(" ", strip=True)[len(s.get_text(" ", strip=True)):].strip()
        dur = re.search(r"\d+", datos.get("Duración", ""))
        return {
            "titulo_original": datos.get("Título Original"),
            "genero": datos.get("Género"),
            "director": datos.get("Director"),
            "duracion_min": int(dur.group()) if dur else None,
            "calificacion": datos.get("Calificación"),
            "distribuidora": datos.get("Distribuidora"),
        }

    def arbol(self, film_id: int) -> dict:
        return self._get(API.format(film_id=film_id, house_id=HOUSE_ID)).json()

    def scrapear(self, log=print) -> dict[str, list[dict]]:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
        log("Showcase IMAX: leyendo cartelera IMAX…")
        cartelera = self.cartelera_imax()
        log(f"  {len(cartelera)} películas en IMAX")

        peliculas, funciones = [], []
        for i, p in enumerate(cartelera, 1):
            fid = p["pelicula_id"]
            detalle = self._detalle(fid)
            time.sleep(PAUSA)
            arbol = self.arbol(fid)
            time.sleep(PAUSA)

            n = 0
            fechas = []
            for fecha, cines in sorted((arbol.get("days") or {}).items()):
                for cine in cines:
                    # La API puede devolver otros cines de la cadena: nos quedamos con IMAX.
                    if cine.get("id") != CINE_IMAX_ID and "imax" not in cine.get("name", "").lower():
                        continue
                    for fmt in cine.get("formats", []):
                        desc = fmt.get("formatDescription", "")       # ej. "IMAX-Subtitulado"
                        formato, _, idioma = desc.partition("-")
                        for perf in fmt.get("performances", []):
                            hora = perf["showTime"]
                            # "N 00:30" = función de trasnoche (pasada la medianoche).
                            trasnoche = hora.startswith("N ")
                            funciones.append({
                                "cadena": CADENA,
                                "cine": CINE["cine"],
                                "ciudad": CINE["ciudad"],
                                "pelicula": arbol.get("name") or p["titulo"],
                                "pelicula_id": fid,
                                "fecha": fecha,
                                "hora": hora[2:] if trasnoche else hora,
                                "trasnoche": "Sí" if trasnoche else "No",
                                "sala": "IMAX",
                                "formato": formato.strip() or desc,
                                "idioma": idioma.strip() or None,
                                "funcion_id": perf.get("performanceId"),
                                "url_compra": (
                                    f"{ENTRADAS}/pelicula.aspx?filmid={fid}&perf={perf.get('performanceId')}"
                                    f"&cinema={cine.get('id')}&date={fecha}&show={fmt.get('showId')}"
                                ),
                            })
                            n += 1
                            fechas.append(fecha)
            log(f"  [{i}/{len(cartelera)}] {p['titulo']}: {n} funciones")

            peliculas.append({
                "cadena": CADENA,
                "pelicula_id": fid,
                "titulo": p["titulo"],
                "estado": "Preventa" if p["anticipada"] else "En cartelera",
                "estreno": min(fechas) if fechas else None,   # primera función IMAX
                "duracion_min": detalle["duracion_min"],
                "calificacion": detalle["calificacion"],
                "formatos": "IMAX",
                "titulo_original": detalle["titulo_original"],
                "genero": detalle["genero"],
                "director": detalle["director"],
                "distribuidora": detalle["distribuidora"],
                "poster": p["poster"],
                "url": f"{ENTRADAS}/pelicula?filmid={fid}&house_id={HOUSE_ID}",
            })

        cines = [{"cadena": CADENA, **CINE}]
        for tabla in (peliculas, cines, funciones):
            for r in tabla:
                r["actualizado"] = ahora
        return {"Peliculas": peliculas, "Cines": cines, "Funciones": funciones}
