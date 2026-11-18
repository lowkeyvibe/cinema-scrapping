"""Monitor de novedades de Showcase IMAX (IMAX Theatre, Norcenter).

En cada corrida saca una "foto" del sitio, la compara con la foto anterior
(guardada en un JSON) y devuelve la lista de avisos a mandar.

Qué vigila:
- funciones   horarios IMAX (nuevos, cancelados, cambiados) vía la API de Voy al Cine
- peliculas   películas que entran/salen de la cartelera IMAX, inicio de preventa
- promos      promociones que mencionan IMAX (/promocion)
- sorteos     sorteos nuevos, que suelen ser kits de merch (/sorteo)
- banners     banners nuevos del carrusel de la home (combos, merch, eventos)
- candy       imagen de la página /candy
- precios     precios IMAX (los publica cron/data.js)

Si una sección no se puede leer (sitio caído, cambio de HTML) se conserva la
foto anterior de esa sección y no se avisa nada, para evitar falsos avisos.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from src.monitor.titulos import en_ingles, guardar_cache, poster
from src.scrapers.showcase_imax import CINE_IMAX_ID, ENTRADAS, HOUSE_ID, SITE, ShowcaseImaxScraper

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MARGEN_CANCELADA = timedelta(hours=1)  # una función que "desaparece" a menos de 1h de empezar no es cancelación
SECCIONES = ("peliculas", "funciones", "promos", "sorteos", "banners", "candy", "precios")
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


# Solo interesan las funciones de noche: desde esta hora, más las de trasnoche (después de medianoche).
HORA_DESDE = "20:30"
CINE_IMAX = "IMAX - Norcenter"
MAX_FUNCIONES = 8      # bloques por aviso, igual que en Cinemark; el resto, con el link a la película
SANGRIA = "\u00a0" * 5


@dataclass
class Aviso:
    texto: str              # HTML de Telegram
    imagen: str | None = None
    # Botones con link, en filas: [[{"text": "...", "url": "..."}, ...], ...]
    botones: list[list[dict]] = field(default_factory=list)


# --------------------------------------------------------------------------- foto del sitio

class Fotografo:
    def __init__(self) -> None:
        self.scraper = ShowcaseImaxScraper()

    def _soup(self, path: str) -> BeautifulSoup:
        return BeautifulSoup(self.scraper._get(urljoin(SITE, path)).text, "lxml")

    def peliculas_y_funciones(self) -> tuple[dict, dict]:
        peliculas, funciones = {}, {}
        for p in self.scraper.cartelera_imax():
            fid = p["pelicula_id"]
            peliculas[str(fid)] = {
                "titulo": p["titulo"],
                "estado": "Preventa" if p["anticipada"] else "En cartelera",
                "poster": p["poster"],
                "url": f"{ENTRADAS}/pelicula?filmid={fid}&house_id={HOUSE_ID}",
            }
            arbol = self.scraper.arbol(fid)
            for fecha, cines in (arbol.get("days") or {}).items():
                for cine in cines:
                    if cine.get("id") != CINE_IMAX_ID and "imax" not in cine.get("name", "").lower():
                        continue
                    for fmt in cine.get("formats", []):
                        for perf in fmt.get("performances", []):
                            hora = perf["showTime"]
                            funciones[str(perf["performanceId"])] = {
                                "pelicula_id": str(fid),
                                "pelicula": arbol.get("name") or p["titulo"],
                                "fecha": fecha,
                                "hora": hora[2:] if hora.startswith("N ") else hora,
                                "trasnoche": hora.startswith("N "),
                                "formato": fmt.get("formatDescription"),
                                "show": fmt.get("showId"),
                                "cinema": cine.get("id"),
                            }
        if not peliculas:
            raise RuntimeError("la cartelera IMAX vino vacía")
        return peliculas, funciones

    def promos(self) -> dict:
        items = {}
        for p in self._soup("/promocion").select(".row.promocion"):
            datos = {
                "titulo": _txt(p.find("h3")),
                "promo": _txt(p.select_one(".promo")),
                "cines": _txt(p.find("p", string=re.compile("Cines habilitados"))),
                "vigencia": _txt(p.select_one(".vigencia")),
            }
            img = p.select_one(".logo-promocion img")
            datos["imagen"] = urljoin(SITE, img["src"]) if img else None
            if "imax" not in f"{datos['promo']} {datos['cines']}".lower():
                continue
            items[f"{datos['titulo']} | {datos['promo']}"] = datos
        if not items:
            raise RuntimeError("no se encontraron promos IMAX")
        return items

    def sorteos(self) -> dict:
        items = {}
        for s in self._soup("/sorteo").select(".listado-sorteos .sorteo"):
            img = s.select_one(".img-sorteo img")
            titulo = _txt(s.find("h2"))
            items[titulo] = {
                "titulo": titulo,
                "descripcion": _txt(s.select_one(".description-sorteo")),
                "imagen": urljoin(SITE, img["src"]) if img else None,
            }
        return items  # puede estar vacío: a veces no hay sorteos

    def banners(self) -> dict:
        items = {}
        for it in self._soup("/").select("#carousel-home .item"):
            img = it.find("img")
            if not img or not img.get("src"):
                continue
            a = it.find("a")
            src = urljoin(SITE, img["src"])
            items[src] = {"imagen": src, "link": a.get("href") if a else None}
        if not items:
            raise RuntimeError("no se encontró el carrusel de la home")
        return items

    def candy(self) -> dict:
        soup = self._soup("/candy")
        cont = soup.select_one("#content") or soup
        imgs = [urljoin(SITE, i["src"]) for i in cont.find_all("img", src=True)
                if "/banners/" not in i["src"] and "afip" not in i["src"]]
        if not imgs:
            raise RuntimeError("no se encontró contenido en /candy")
        return {src: {"imagen": src} for src in imgs}

    def precios(self) -> dict:
        js = self.scraper._get(SITE + "/cron/data.js").text
        m = re.search(r"let\s+ImaxPrices\s*=\s*\[(.*?)\];", js, re.S)
        if not m:
            raise RuntimeError("no se encontró ImaxPrices en data.js")
        precios = {}
        for bloque in re.split(r"\n\s*\{\s*\n\s*id:", m.group(1))[1:]:
            t = re.search(r'title:\s*"([^"]*)"', bloque)
            valores = [_limpiar(v) for v in re.findall(r'(?:general|ninos|promo|matinee):\s*"([^"]*)"', bloque)]
            if t:
                precios[_limpiar(t.group(1))] = " / ".join(v for v in valores if v)
        if not precios:
            raise RuntimeError("ImaxPrices vino vacío")
        return precios

    def sacar(self, log=print) -> dict:
        foto: dict = {}
        try:
            foto["peliculas"], foto["funciones"] = self.peliculas_y_funciones()
        except Exception as e:  # noqa: BLE001
            log(f"  ! cartelera/funciones: {e}")
        for seccion in ("promos", "sorteos", "banners", "candy", "precios"):
            try:
                foto[seccion] = getattr(self, seccion)()
            except Exception as e:  # noqa: BLE001
                log(f"  ! {seccion}: {e}")
        return foto


# --------------------------------------------------------------------------- comparación

def comparar(antes: dict, ahora: dict, momento: datetime | None = None) -> list[Aviso]:
    momento = momento or datetime.now(TZ)
    avisos: list[Aviso] = []

    if "peliculas" in ahora and "peliculas" in antes:
        avisos += _avisos_cartelera(antes["peliculas"], ahora["peliculas"],
                                    antes.get("funciones", {}), ahora["funciones"], momento)

    if "promos" in ahora and "promos" in antes:
        for k in ahora["promos"].keys() - antes["promos"].keys():
            p = ahora["promos"][k]
            avisos.append(Aviso(
                f"🏷️ <b>Promo nueva IMAX</b> — {_e(p['titulo'])}\n{_e(p['promo'])}\n"
                f"<i>{_e(p['vigencia'])}</i>\n{SITE}/promocion"))
        for k in antes["promos"].keys() - ahora["promos"].keys():
            p = antes["promos"][k]
            avisos.append(Aviso(f"🏷️ <b>Promo IMAX que ya no está</b> — {_e(p['titulo'])}\n{_e(p['promo'])}"))
        for k in antes["promos"].keys() & ahora["promos"].keys():
            if antes["promos"][k]["vigencia"] != ahora["promos"][k]["vigencia"]:
                p = ahora["promos"][k]
                avisos.append(Aviso(
                    f"🏷️ <b>Cambió la vigencia de una promo IMAX</b> — {_e(p['titulo'])}\n{_e(p['promo'])}\n"
                    f"Antes: {_e(antes['promos'][k]['vigencia'])}\nAhora: {_e(p['vigencia'])}"))

    if "sorteos" in ahora and "sorteos" in antes:
        for k in ahora["sorteos"].keys() - antes["sorteos"].keys():
            s = ahora["sorteos"][k]
            avisos.append(Aviso(f"🎁 <b>Sorteo nuevo</b> — {_e(s['titulo'])}\n{_e(s['descripcion'])}\n{SITE}/sorteo",
                                s["imagen"]))

    if "banners" in ahora and "banners" in antes:
        for k in ahora["banners"].keys() - antes["banners"].keys():
            b = ahora["banners"][k]
            avisos.append(Aviso(f"🖼️ <b>Banner nuevo en la home de Showcase</b>\n{_e(b['link'] or SITE)}", b["imagen"]))

    if "candy" in ahora and "candy" in antes:
        for k in ahora["candy"].keys() - antes["candy"].keys():
            avisos.append(Aviso(f"🍿 <b>Cambió la página de Candy</b> (combos / merch)\n{SITE}/candy", k))

    if "precios" in ahora and "precios" in antes and ahora["precios"] != antes["precios"]:
        lineas = []
        for k in sorted(antes["precios"].keys() | ahora["precios"].keys()):
            a, b = antes["precios"].get(k), ahora["precios"].get(k)
            if a != b:
                lineas.append(f"<b>{_e(k)}</b>\nAntes: {_e(a or '—')}\nAhora: {_e(b or '—')}")
        avisos.append(Aviso("💲 <b>Cambiaron los precios IMAX</b>\n\n" + "\n\n".join(lineas) + f"\n\n{SITE}/precios"))

    guardar_cache()
    return avisos


def _avisos_cartelera(p_antes, p_ahora, f_antes, f_ahora, momento) -> list[Aviso]:
    avisos = []
    pelis = {**p_antes, **p_ahora}

    def img(fid: str, titulo: str | None = None) -> str | None:
        """Póster de TMDB (más grande, arte en inglés) o, si no hay, el de Showcase."""
        p = pelis.get(fid) or {}
        return poster(titulo or p.get("titulo"), momento.year) or p.get("poster")
    nuevas_pelis = p_ahora.keys() - p_antes.keys()

    # Funciones nuevas (solo de noche), agrupadas por película.
    nuevas_por_peli: dict[str, list[dict]] = {}
    for k in f_ahora.keys() - f_antes.keys():
        f = {**f_ahora[k], "perf": k}
        if es_relevante(f):
            nuevas_por_peli.setdefault(f["pelicula_id"], []).append(f)

    for fid in nuevas_pelis:
        p = p_ahora[fid]
        funcs = nuevas_por_peli.pop(fid, [])
        if funcs:
            # Igual que Cinemark: el póster ya dice qué película es.
            txt = "<b>🟢🟢 Tickets are on sale now!! 🟢🟢</b>\n\n" + _bloques(funcs, fid, momento)
        else:
            txt = (f"🎬 <b>Nueva en IMAX: {_e(_peli(p['titulo'], momento))}</b>\n\n"
                   f"Todavía no hay funciones subtituladas desde las {HORA_DESDE}." + _pie(p["url"]))
        avisos.append(Aviso(txt, img(fid)))

    for fid, funcs in nuevas_por_peli.items():
        p = p_ahora.get(fid) or {"titulo": funcs[0]["pelicula"], "url": ""}
        avisos.append(Aviso(f"🎟️ <b>Funciones nuevas: {_e(_peli(p['titulo'], momento))}</b>\n\n"
                            + _bloques(funcs, fid, momento), img(fid, p["titulo"])))

    # Funciones que desaparecen antes de empezar => canceladas.
    canceladas: dict[str, list[dict]] = {}
    for k in f_antes.keys() - f_ahora.keys():
        f = f_antes[k]
        if es_relevante(f) and _inicio(f) > momento + MARGEN_CANCELADA:
            canceladas.setdefault(f["pelicula_id"], []).append(f)
    for fid, funcs in canceladas.items():
        titulo = _peli(funcs[0]["pelicula"], momento)
        avisos.append(Aviso(f"❌ <b>Funciones IMAX que ya no están: {_e(titulo)}</b>\n\n{_listar(funcs)}",
                            img(fid, funcs[0]["pelicula"])))

    # Misma función con otro horario/fecha.
    cambios, pelis_cambiadas = [], set()
    for k in f_antes.keys() & f_ahora.keys():
        a, b = f_antes[k], f_ahora[k]
        if (a["fecha"], a["hora"]) != (b["fecha"], b["hora"]) and (es_relevante(a) or es_relevante(b)):
            cambios.append(f"• {_e(_peli(b['pelicula'], momento))}: {_fecha(a)} → {_fecha(b)}")
            pelis_cambiadas.add((b["pelicula_id"], b["pelicula"]))
    if cambios:
        # Con una sola película, va su póster; si son varias, sin imagen.
        unica = next(iter(pelis_cambiadas)) if len(pelis_cambiadas) == 1 else None
        avisos.append(Aviso("🔄 <b>Cambios de horario en IMAX</b>\n\n" + "\n".join(cambios),
                            img(*unica) if unica else None))

    # Películas que pasan de preventa a cartelera, o salen de cartelera.
    for fid in p_antes.keys() & p_ahora.keys():
        if p_antes[fid]["estado"] != p_ahora[fid]["estado"]:
            p = p_ahora[fid]
            avisos.append(Aviso(f"📣 <b>{_e(_peli(p['titulo'], momento))}</b>: {p_antes[fid]['estado']} → {p['estado']}"
                                + _pie(p["url"]), img(fid)))
    for fid in p_antes.keys() - p_ahora.keys():
        avisos.append(Aviso(f"👋 <b>Salió de la cartelera IMAX:</b> {_e(_peli(p_antes[fid]['titulo'], momento))}",
                            img(fid)))

    return avisos


def resumen_inicial(foto: dict) -> Aviso:
    pelis = foto.get("peliculas", {})
    funcs = foto.get("funciones", {})
    lineas = []
    for fid, p in pelis.items():
        n = sum(1 for f in funcs.values() if f["pelicula_id"] == fid and es_relevante(f))
        lineas.append(f"• {_e(_peli(p['titulo'], datetime.now(TZ)))} — {p['estado']}, {n} funciones desde las {HORA_DESDE}")
    aviso = Aviso(
        "✅ <b>Monitor de Showcase IMAX activado</b>\n"
        "Te voy a avisar de funciones nuevas o canceladas, preventas, promos, sorteos/merch, "
        "banners nuevos y cambios de precio.\n\n"
        "<b>Cartelera IMAX hoy:</b>\n" + ("\n".join(lineas) or "(vacía)"))
    guardar_cache()
    return aviso


# --------------------------------------------------------------------------- helpers

def _peli(titulo: str, momento: datetime) -> str:
    """Nombre de la película en inglés (TMDB); si no se encuentra, el de Showcase."""
    return en_ingles(titulo, momento.year)


def _url_funcion(fid: str, perf_id: str, f: dict) -> str | None:
    """Link directo a la función: es el mismo al que lleva el botón "Confirmar" del sitio."""
    if not f.get("show") or f.get("cinema") is None:
        return None
    return (f"{ENTRADAS}/pelicula.aspx?filmid={fid}&perf={perf_id}"
            f"&cinema={f['cinema']}&date={f['fecha']}&show={f['show']}")


def es_relevante(f: dict) -> bool:
    """Las únicas funciones que interesan: de noche y subtituladas (como en Cinemark)."""
    formato = (f.get("formato") or "").lower()
    return es_de_noche(f) and "castellano" not in formato and "doblad" not in formato


def _bloques(funcs: list[dict], fid: str, momento: datetime) -> str:
    """Mismo formato que Cinemark (sin asientos libres: Showcase no los publica):

        📍 <b>IMAX - Norcenter</b>
        <a href="…">IMAX 🍿 · 22:35 · Subtitulada</a>
             Jue 26/11
    """
    futuras = [f for f in sorted(funcs, key=_inicio) if _inicio(f) > momento][:MAX_FUNCIONES]
    return f"📍 <b>{CINE_IMAX}</b>\n" + _separados(_bloque(f, fid) for f in futuras)


SEPARADOR = "------------------------"


def _separados(bloques) -> str:
    """Bloques con una línea de guiones antes, entre y después de cada uno."""
    return "\n".join([SEPARADOR] + [x for b in bloques for x in (b, SEPARADOR)])


def _bloque(f: dict, fid: str) -> str:
    formato, _, idioma = (f.get("formato") or "IMAX").partition("-")
    idioma = {"subtitulado": "Subtitulada", "castellano": "Doblada al castellano"}.get(idioma.strip().lower(), idioma.strip())
    cabeza = " · ".join(x for x in ("IMAX 🍿", f["hora"], idioma) if x)
    url = _url_funcion(fid, f["perf"], f) if f.get("perf") else None
    if url:
        cabeza = f'<a href="{html.escape(url)}">{cabeza}</a>'
    d = datetime.strptime(f["fecha"], "%Y-%m-%d")
    dia = f"{DIAS[d.weekday()]} {d:%d/%m}" + (" (trasnoche)" if f.get("trasnoche") else "")
    return cabeza + "\n" + SANGRIA + dia


def es_de_noche(f: dict) -> bool:
    """Desde HORA_DESDE hasta medianoche, o trasnoche (pasada la medianoche, antes de las 6)."""
    return f.get("trasnoche") or f["hora"] >= HORA_DESDE or f["hora"] < "06:00"


def _inicio(f: dict) -> datetime:
    d = datetime.strptime(f"{f['fecha']} {f['hora']}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    return d + timedelta(days=1) if f.get("trasnoche") else d


def _fecha(f: dict) -> str:
    d = datetime.strptime(f["fecha"], "%Y-%m-%d")
    return f"{DIAS[d.weekday()]} {d:%d/%m} {f['hora']}" + (" (trasnoche)" if f.get("trasnoche") else "")


def _listar(funcs: list[dict], fid: str | None = None, momento: datetime | None = None) -> str:
    """Una línea por día: 'Mié 25/11: 20:30, 22:35'.

    Con `fid`, cada horario futuro es un link directo a comprar esa función.
    """
    por_dia: dict[str, list[dict]] = {}
    for f in sorted(funcs, key=_inicio):
        por_dia.setdefault(f["fecha"], []).append(f)
    lineas = []
    for fecha, fs in por_dia.items():
        d = datetime.strptime(fecha, "%Y-%m-%d")
        horas = []
        for f in fs:
            hora = f["hora"] + (" (trasnoche)" if f.get("trasnoche") else "")
            url = _url_funcion(fid, f["perf"], f) if fid and f.get("perf") else None
            if url and (momento is None or _inicio(f) > momento):
                hora = f'<a href="{html.escape(url)}">{hora}</a>'
            horas.append(hora)
        lineas.append(f"• {DIAS[d.weekday()]} {d:%d/%m}: {', '.join(horas)}")
    return "\n".join(lineas)


def _pie(url_peli: str) -> str:
    return f'\n\n🎟️ <a href="{html.escape(url_peli)}">Ver película y todas las funciones</a>' if url_peli else ""


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _limpiar(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)
