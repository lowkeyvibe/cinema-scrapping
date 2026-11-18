"""Monitor de novedades de Cinemark Argentina.

Misma idea que showcase_imax.py: en cada corrida saca una "foto" del sitio,
la compara con la anterior (state/cinemark.json) y devuelve los avisos.

Qué vigila:
- peliculas   películas nuevas, cambios de estado (próximamente → preventa → cartelera), salidas
- funciones   funciones nuevas, canceladas y cambios de horario en los cines de CINES
- promos      promociones nuevas, que terminan o cambian (solo las que aplican a CINES)
- merch       productos nuevos de la tienda, que se agotan/salen o cambian de precio

Todo sale de la API JSON bff.cinemark.com.ar, salvo el merch: la tienda
solo carga sus productos después de elegir un cine en el navegador, así que
se lee con Playwright y solo cuando se pide (incluir_merch=True), porque es
lo más lento.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from src.scrapers.cinemark import SITE, CinemarkScraper

from .showcase_imax import DIAS, HORA_DESDE, TZ, Aviso, _e, _separados, info_peli, termina
from .titulos import detalles, en_ingles, guardar_cache, poster

SECCIONES = ("peliculas", "funciones", "promos", "merch")
MARGEN_CANCELADA = timedelta(hours=1)
# Solo se vigilan estos cines (id de la API → nombre). Para sumar uno, ver los ids en
# https://bff.cinemark.com.ar/api/cinema/theaters (con el encabezado country: AR).
CINES = {103: "Abasto", 111: "DOT"}
CINE_MERCH = "Abasto"  # la tienda pide elegir un cine; el catálogo es el mismo en todos
MAX_LINEAS = 25        # para que un aviso no se vuelva interminable
MAX_POR_CINE = 8       # bloques de función por cine en un aviso; el resto se ve con el link a la película

# Asientos libres de la última foto: {clave de función: (libres, capacidad)}.
# Cambian en cada corrida, así que NO van a state/ (harían un commit cada 30 min):
# solo se usan para armar el texto de los avisos.
ASIENTOS: dict[str, tuple[int | None, int | None]] = {}
ESTADOS = {"SHOWING_NOW": "En cartelera", "PRESALE": "Preventa", "COMING_SOON": "Próximamente"}


# --------------------------------------------------------------------------- foto del sitio

class Fotografo:
    def __init__(self, incluir_merch: bool = False) -> None:
        self.scraper = CinemarkScraper()
        self.incluir_merch = incluir_merch

    @property
    def intentadas(self) -> tuple[str, ...]:
        """Secciones que esta corrida intenta leer (el merch no se lee siempre)."""
        return SECCIONES if self.incluir_merch else tuple(s for s in SECCIONES if s != "merch")

    def peliculas(self) -> dict:
        items = {}
        for m in self.scraper._get("movies"):
            items[str(m["corporateId"])] = {
                "titulo": m["title"],
                "estado": ESTADOS.get(m.get("status"), m.get("status")),
                "estreno": (m.get("openingDate") or "")[:10],
                "poster": m.get("posterUrl"),
                "url": f"{SITE}/pelicula/{m['slug']}",
            }
        if not items:
            raise RuntimeError("la lista de películas vino vacía")
        return items

    def funciones(self) -> dict:
        items = {}
        ASIENTOS.clear()
        for cine in self.scraper._get("theaters", limit=1000):
            if cine["id"] not in CINES:
                continue
            for s in self.scraper._get("showtimes", theater=cine["id"]):
                clave = f"{cine['id']}-{s['sessionId']}-{s.get('sessionFormat')}"
                items[clave] = {
                    "pelicula_id": str(s.get("corporateId")),
                    "pelicula": s.get("movieName"),
                    "cine": cine["name"],
                    # La API marca la hora como UTC pero es hora local argentina.
                    "inicio": s["sessionDateTime"][:16],
                    "formato": s.get("sessionFormat"),
                    "idioma": (s.get("language") or {}).get("shortName"),
                    "sala": s.get("theaterRoom"),
                }
                occ = s.get("occupation") or {}
                ASIENTOS[clave] = (occ.get("availableSeats"), occ.get("capacity"))
        if not items:
            raise RuntimeError("no vino ninguna función")
        return items

    def promos(self) -> dict:
        r = self.scraper.session.get(f"{SITE.replace('www', 'bff')}/api/content/promotions",
                                     params={"includeAll": "false", "sortBy": "order", "order": "ASC"},
                                     timeout=30)
        r.raise_for_status()
        items = {}
        for p in r.json()["data"]:
            cines = [n for i, n in CINES.items() if i in (p.get("theaterIds") or [])]
            if not p.get("active", True) or not cines:
                continue
            items[str(p["id"])] = {
                "cines": cines,
                "titulo": p.get("title") or "",
                "descripcion": p.get("description") or "",
                "info": p.get("additionalInformation") or "",
                "hasta": (p.get("endDate") or "")[:10],
                "imagen": p.get("image"),
            }
        if not items:
            raise RuntimeError("la lista de promos vino vacía")
        return items

    def merch(self) -> dict:


        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            nav = pw.chromium.launch()
            try:
                page = nav.new_page(viewport={"width": 1280, "height": 1000}, locale="es-AR")
                page.goto(f"{SITE}/store", wait_until="domcontentloaded", timeout=60000)
                # La tienda abre un panel lateral "Elegí tu cine principal".
                panel = page.locator(".MuiDrawer-root")
                panel.get_by_text(CINE_MERCH, exact=True).wait_for(timeout=30000)
                boton = page.get_by_test_id("teather-appply-button")  # "SELECCIONAR" (sic)
                with page.expect_response(lambda r: "/api/merch-store" in r.url, timeout=60000) as resp:
                    panel.get_by_text(CINE_MERCH, exact=True).click()
                    # El botón está deshabilitado hasta que se marca un cine.
                    page.wait_for_function("b => !b.disabled", arg=boton.element_handle(), timeout=15000)
                    # El aviso de cookies y el propio panel quedan encima del botón y se
                    # comen el clic del mouse, así que lo disparamos desde JavaScript.
                    boton.evaluate("b => b.click()")
                categorias = resp.value.json()
            finally:
                nav.close()

        if isinstance(categorias, dict):
            categorias = categorias.get("data") or []
        items: dict = {}
        for cat in categorias:
            for m in cat.get("merchandisings") or []:
                codigo = m["strMCode"]
                if codigo in items:
                    continue
                imgs = m.get("jsonImages") or []
                items[codigo] = {
                    "titulo": (m.get("strTitle") or "").strip(),
                    "precio": round((m.get("salePrice") or 0) / 100),  # viene en centavos
                    "categoria": cat.get("strTitle"),
                    "imagen": imgs[0]["image"] if imgs else None,
                }
        if not items:
            raise RuntimeError("la tienda no devolvió productos")
        return items

    def sacar(self, log=print) -> dict:
        foto: dict = {}
        for seccion in self.intentadas:
            try:
                foto[seccion] = getattr(self, seccion)()
            except Exception as e:  # noqa: BLE001
                log(f"  ! {seccion}: {e}")
        return foto


# --------------------------------------------------------------------------- comparación

def comparar(antes: dict, ahora: dict, momento: datetime | None = None) -> list[Aviso]:
    momento = momento or datetime.now(TZ)
    avisos: list[Aviso] = []

    def ambas(s):
        return s in antes and s in ahora

    if ambas("peliculas"):
        # Si una película nueva ya trae funciones, el aviso "¡Salieron las entradas!" la cubre.
        con_funciones = ({f["pelicula_id"] for f in ahora["funciones"].values()}
                         if ambas("funciones") else set())
        avisos += _avisos_peliculas(antes["peliculas"], ahora["peliculas"], con_funciones)
    if ambas("funciones"):
        # La foto anterior puede tener funciones de cines que ya no se vigilan: no son cancelaciones.
        ids = tuple(f"{i}-" for i in CINES)
        f_antes = {k: f for k, f in antes["funciones"].items() if k.startswith(ids)}
        avisos += _avisos_funciones(f_antes, ahora["funciones"],
                                    ahora.get("peliculas") or antes["peliculas"], momento)
    if ambas("promos"):
        # Las promos de la foto anterior pueden ser de cines que ya no se vigilan: no "terminaron".
        # (Las de fotos viejas sin "cines" solo se comparan si siguen apareciendo.)
        nombres = set(CINES.values())
        p_antes = {k: p for k, p in antes["promos"].items()
                   if (set(p["cines"]) & nombres if "cines" in p else k in ahora["promos"])}
        avisos += _avisos_promos(p_antes, ahora["promos"])
    if ambas("merch"):
        avisos += _avisos_merch(antes["merch"], ahora["merch"])
    for a in avisos:
        _links_en_texto(a)
    guardar_cache()
    return avisos


def _avisos_peliculas(antes: dict, ahora: dict, con_funciones: set[str]) -> list[Aviso]:
    avisos = []
    for k in ahora.keys() - antes.keys() - con_funciones:
        p = ahora[k]
        txt = f"🎬 <b>Película nueva en Cinemark: {_peli(p)}</b>\n{p['estado']}"
        if p["estreno"]:
            txt += f" · estreno {_dia(p['estreno'])}"
        if p["estado"] == "Preventa":
            txt += "\n🔥 ¡Ya está en preventa!"
        avisos.append(Aviso(txt, _poster(p), _boton("🎟️ Ver película y todas las funciones", p["url"])))
    for k in antes.keys() & ahora.keys():
        a, p = antes[k], ahora[k]
        if a["estado"] != p["estado"]:
            fuego = "🔥 " if p["estado"] == "Preventa" else ""
            avisos.append(Aviso(f"📣 {fuego}<b>{_peli(p)}</b>: {a['estado']} → {p['estado']}",
                                _poster(p), _boton("🎟️ Ver película y todas las funciones", p["url"])))
        elif a["estreno"] != p["estreno"] and a["estreno"] and p["estreno"]:
            avisos.append(Aviso(f"📅 <b>{_peli(p)}</b>: cambió el estreno "
                                f"{_dia(a['estreno'])} → {_dia(p['estreno'])}",
                                _poster(p), _boton("🎬 Ver película", p["url"])))
    for k in antes.keys() - ahora.keys():
        avisos.append(Aviso(f"👋 <b>Salió de Cinemark:</b> {_peli(antes[k])}", _poster(antes[k])))
    return avisos


def _avisos_funciones(antes: dict, ahora: dict, peliculas: dict, momento: datetime) -> list[Aviso]:
    """Solo avisa de funciones que interesan (ver es_relevante); la foto guarda todas."""
    avisos = []
    pelis_con_funciones_antes = {f["pelicula_id"] for f in antes.values()}

    # Funciones nuevas: un resumen por película.
    nuevas: dict[str, list[dict]] = {}
    for k in ahora.keys() - antes.keys():
        nuevas.setdefault(ahora[k]["pelicula_id"], []).append({**ahora[k], "clave": k})
    primeras, resto = [], []
    for pid, fs in sorted(nuevas.items(), key=lambda x: -len(x[1])):
        peli = peliculas.get(pid) or {"titulo": fs[0]["pelicula"]}
        titulo = _peli(peli, momento)
        noche = [f for f in fs if es_relevante(f)]
        if pid not in pelis_con_funciones_antes:
            # Primera vez que la película tiene funciones: se avisa aunque no haya de noche.
            # El póster ya dice qué película es: el título queda corto.
            det = _detalles(peli, momento)
            txt = "<b>🟢🟢 Tickets are on sale now!! 🟢🟢</b>" + info_peli(det) + "\n\n"
            if noche:
                txt += _bloques(noche, peli.get("url", ""), det["duracion"])
                botones = []
            else:
                txt += f"Todavía no hay funciones subtituladas desde las {HORA_DESDE}."
                botones = _boton("🎟️ Ver película y todas las funciones", peli.get("url", ""))
            # Va el fondo panorámico de la película (si TMDB lo tiene); si no, el póster.
            primeras.append(Aviso(txt, det["fondo"] or _poster(peli, momento), botones))
        elif noche:
            # Un aviso por película, con un bloque por función.
            det = _detalles(peli, momento)
            resto.append(Aviso(f"🎟️ <b>Funciones nuevas: {titulo}</b>" + info_peli(det) + "\n\n"
                               + _bloques(noche, peli.get("url", ""), det["duracion"]),
                               _poster(peli, momento)))
    avisos += primeras + resto

    # Funciones que desaparecen antes de empezar => canceladas.
    canceladas: dict[tuple[str, str], list[dict]] = {}
    pelis_canceladas: dict[str, dict] = {}
    for k in antes.keys() - ahora.keys():
        f = antes[k]
        if es_relevante(f) and _inicio(f) > momento + MARGEN_CANCELADA:
            canceladas.setdefault((_peli_de(f, peliculas, momento), f["cine"]), []).append(f)
            pelis_canceladas[f["pelicula_id"]] = peliculas.get(f["pelicula_id"]) or {"titulo": f["pelicula"]}
    if canceladas:
        lineas = [f"• <b>{peli}</b> — {_e(cine)}: "
                  + ", ".join(_cuando(f) for f in sorted(fs, key=_inicio)[:6])
                  + (f" y {len(fs) - 6} más" if len(fs) > 6 else "")
                  for (peli, cine), fs in sorted(canceladas.items())]
        avisos.append(Aviso("❌ <b>Funciones canceladas en Cinemark</b>\n\n" + _recortar(lineas),
                            _poster_si_una(list(pelis_canceladas.values()), momento)))

    # Misma función con otro horario (si era o pasa a ser de noche).
    movidas = [k for k in antes.keys() & ahora.keys()
               if antes[k]["inicio"] != ahora[k]["inicio"] and (es_relevante(antes[k]) or es_relevante(ahora[k]))]
    cambios = [f"• <b>{_peli_de(ahora[k], peliculas, momento)}</b> — {_e(ahora[k]['cine'])}: "
               f"{_cuando(antes[k])} → {_cuando(ahora[k])}" for k in movidas]
    if cambios:
        pelis = {ahora[k]["pelicula_id"]: peliculas.get(ahora[k]["pelicula_id"]) or {"titulo": ahora[k]["pelicula"]}
                 for k in movidas}
        avisos.append(Aviso("🔄 <b>Cambios de horario en Cinemark</b>\n\n" + _recortar(sorted(cambios)),
                            _poster_si_una(list(pelis.values()), momento)))
    return avisos


def _avisos_promos(antes: dict, ahora: dict) -> list[Aviso]:
    avisos = []
    for k in ahora.keys() - antes.keys():
        p = ahora[k]
        txt = f"🏷️ <b>Promo nueva en Cinemark</b> — {_e(p['titulo'])}\n{_e(p['descripcion'])}"
        if p["info"]:
            txt += f"\n<i>{_e(p['info'])}</i>"
        if p["hasta"]:
            txt += f"\nHasta el {_dia(p['hasta'])}"
        txt += f"\n📍 {_cines(p['cines'])}"
        avisos.append(Aviso(txt, p.get("imagen"), _boton("🏷️ Ver promociones", f"{SITE}/promociones")))
    for k in antes.keys() - ahora.keys():
        avisos.append(Aviso(f"🏷️ <b>Terminó una promo de Cinemark</b> — {_e(antes[k]['titulo'])}"))
    for k in antes.keys() & ahora.keys():
        a, p = antes[k], ahora[k]
        cambios = [f"{campo}: {_e(a[campo] or '—')} → {_e(p[campo] or '—')}"
                   for campo in ("titulo", "descripcion", "info", "hasta") if a[campo] != p[campo]]
        if cambios:
            avisos.append(Aviso(f"🏷️ <b>Cambió una promo de Cinemark</b> — {_e(p['titulo'])}\n"
                                + "\n".join(cambios), botones=_boton("🏷️ Ver promociones", f"{SITE}/promociones")))
    return avisos


def _avisos_merch(antes: dict, ahora: dict) -> list[Aviso]:
    avisos = []
    for k in ahora.keys() - antes.keys():
        m = ahora[k]
        avisos.append(Aviso(f"🛍️ <b>Merch nuevo en Cinemark</b> — {_e(m['titulo'])}\n"
                            f"{_pesos(m['precio'])} · {_e(m['categoria'] or '')}", m.get("imagen"),
                            _boton("🛍️ Ir a la tienda", f"{SITE}/store")))
    for k in antes.keys() - ahora.keys():
        avisos.append(Aviso(f"🛍️ <b>Ya no está en la tienda de Cinemark:</b> {_e(antes[k]['titulo'])}"))
    precios = [f"• {_e(ahora[k]['titulo'])}: {_pesos(antes[k]['precio'])} → {_pesos(ahora[k]['precio'])}"
               for k in antes.keys() & ahora.keys() if antes[k]["precio"] != ahora[k]["precio"]]
    if precios:
        avisos.append(Aviso("💲 <b>Cambios de precio en el merch de Cinemark</b>\n\n" + _recortar(precios),
                            botones=_boton("🛍️ Ir a la tienda", f"{SITE}/store")))
    return avisos


def resumen_inicial(foto: dict) -> Aviso:
    partes = ["✅ <b>Monitor de Cinemark activado</b>",
              "Te voy a avisar de películas nuevas, preventas, funciones nuevas o canceladas, "
              "cambios de horario, promos y merch.\n"]
    if "peliculas" in foto:
        estados: dict[str, int] = {}
        for p in foto["peliculas"].values():
            estados[p["estado"]] = estados.get(p["estado"], 0) + 1
        partes.append("🎬 " + ", ".join(f"{n} {e.lower()}" for e, n in estados.items()))
    if "funciones" in foto:
        cines = {f["cine"] for f in foto["funciones"].values()}
        partes.append(f"🎟️ {len(foto['funciones'])} funciones en {len(cines)} cines")
    if "promos" in foto:
        partes.append(f"🏷️ {len(foto['promos'])} promos activas")
    if "merch" in foto:
        partes.append(f"🛍️ {len(foto['merch'])} productos en la tienda")
    guardar_cache()
    return Aviso("\n".join(partes))


# --------------------------------------------------------------------------- helpers

def _cines(nombres: list[str]) -> str:
    if len(nombres) == len(CINES):
        return "Todos tus cines (" + ", ".join(CINES.values()) + ")"
    return ", ".join(nombres)


def _boton(texto: str, url: str) -> list[list[dict]]:
    return [[{"text": texto, "url": url}]] if url else []


def _links_en_texto(aviso: Aviso) -> None:
    """Sin botones (el usuario no los quiere): cada uno pasa a ser un link al pie del texto.

    "🎟️ Ver película" → "🎟️ <a href=...>Ver película</a>"
    """
    for fila in aviso.botones:
        for b in fila:
            emoji, _, etiqueta = b["text"].partition(" ")
            aviso.texto += f'\n\n{emoji} <a href="{html.escape(b["url"])}">{_e(etiqueta)}</a>'
    aviso.botones = []


def _peli(p: dict, momento: datetime | None = None) -> str:
    """Título en inglés (vía TMDB, ver titulos.py), ya escapado para HTML.

    El año del estreno ayuda a TMDB a elegir bien; si no hay, se usa el actual.
    """
    anio = (p.get("estreno") or "")[:4] or (momento or datetime.now(TZ)).year
    return _e(en_ingles(p["titulo"], anio))


def _detalles(p: dict, momento: datetime | None = None) -> dict:
    """Fondo panorámico, duración y trailer de TMDB (ver titulos.detalles)."""
    anio = (p.get("estreno") or "")[:4] or (momento or datetime.now(TZ)).year
    return detalles(p["titulo"], anio)


def _poster(p: dict, momento: datetime | None = None) -> str | None:
    """Póster de TMDB (arte en inglés); si no hay, el del sitio de Cinemark."""
    anio = (p.get("estreno") or "")[:4] or (momento or datetime.now(TZ)).year
    return poster(p["titulo"], anio) or p.get("poster")


def _poster_si_una(pelis: list[dict], momento: datetime) -> str | None:
    """En los avisos que juntan varias películas, póster solo si es una sola."""
    return _poster(pelis[0], momento) if len(pelis) == 1 else None


def _peli_de(f: dict, peliculas: dict, momento: datetime) -> str:
    """Título en inglés de la película de una función."""
    return _peli(peliculas.get(f["pelicula_id"]) or {"titulo": f["pelicula"]}, momento)


def es_relevante(f: dict) -> bool:
    """Las únicas funciones que interesan: de noche, subtituladas y sin D-BOX."""
    return (es_de_noche(f) and f.get("idioma") != "CAST"
            and not (f.get("formato") or "").upper().startswith("DBOX"))


def es_de_noche(f: dict) -> bool:
    """Desde HORA_DESDE hasta medianoche, o pasada la medianoche (antes de las 6), como en Showcase."""
    hora = f["inicio"][11:16]
    return hora >= HORA_DESDE or hora < "06:00"


def _bloques(fs: list[dict], url: str = "", duracion: int | None = None) -> str:
    """Un bloque por función, agrupadas por cine y en orden de horario (mismo formato que IMAX):

        📍 <b>CINEMARK - Abasto</b>
        <a href="…">Cinemark 🍿 · 22:45 · Subtitulada</a>
        Mié 16/12 · termina 01:15

    El link lleva a comprar: Cinemark no tiene
    un link por función, así que abre la película y ahí se toca el horario.
    Hasta MAX_POR_CINE funciones por cine.
    """
    por_cine: dict[str, list[dict]] = {}
    for f in sorted(fs, key=lambda f: (f["inicio"], f.get("formato") or "")):
        por_cine.setdefault(f["cine"], []).append(f)
    partes = []
    for cine, funcs in sorted(por_cine.items()):
        bloques = [_bloque(f, url, duracion) for f in funcs[:MAX_POR_CINE]]
        partes.append(f"📍 <b>CINEMARK - {_e(cine)}</b>\n" + _separados(bloques))
    return "\n\n".join(partes)


SANGRIA = "\u00a0" * 5   # espacios que Telegram no recorta al principio de la línea
TIPOS = {"SUB": "Subtitulada", "CAST": "Doblada al castellano"}


def _bloque(f: dict, url: str = "", duracion: int | None = None) -> str:
    cabeza = " · ".join(x for x in ("Cinemark 🍿", f["inicio"][11:16],
                                     TIPOS.get(f.get("idioma"), f.get("idioma") or "")) if x)
    if url:
        cabeza = f'<a href="{html.escape(url)}">{cabeza}</a>'
    return cabeza + "\n" + _dia_corto(f) + termina(f["inicio"][11:16], duracion)


def _lugares(f: dict) -> str:
    libres, capacidad = ASIENTOS.get(f.get("clave", ""), (None, None))
    return f"{libres} 🟢 / {capacidad} 🔴" if libres is not None and capacidad else ""


def _dia_corto(f: dict) -> str:
    """'Mié 16/12'; pasada la medianoche, el día anterior con '(trasnoche)'."""
    d = _inicio(f)
    if f["inicio"][11:16] < "06:00":
        d -= timedelta(days=1)
        return f"{DIAS[d.weekday()]} {d:%d/%m} (trasnoche)"
    return f"{DIAS[d.weekday()]} {d:%d/%m}"


def _inicio(f: dict) -> datetime:
    return datetime.strptime(f["inicio"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)


def _dia(fecha: str) -> str:
    d = datetime.strptime(fecha, "%Y-%m-%d")
    return f"{DIAS[d.weekday()]} {d:%d/%m}"


def _cuando(f: dict) -> str:
    hora = f["inicio"][11:16]
    if hora < "06:00":
        # Pasada la medianoche: en la cartelera figura como trasnoche del día anterior.
        dia = (_inicio(f) - timedelta(days=1)).strftime("%Y-%m-%d")
        return f"{_dia(dia)} {hora} (trasnoche)"
    return f"{_dia(f['inicio'][:10])} {hora}"


def _rango(fs: list[dict]) -> str:
    dias = sorted({f["inicio"][:10] for f in fs})
    return _dia(dias[0]) if len(dias) == 1 else f"{_dia(dias[0])} a {_dia(dias[-1])}"


def _pesos(n: int) -> str:
    return "$" + f"{n:,}".replace(",", ".")


def _recortar(lineas: list[str]) -> str:
    if len(lineas) <= MAX_LINEAS:
        return "\n".join(lineas)
    return "\n".join(lineas[:MAX_LINEAS]) + f"\n… y {len(lineas) - MAX_LINEAS} más"
