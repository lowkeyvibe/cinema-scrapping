"""Monitor de novedades de Cinemark Argentina.

Misma idea que showcase_imax.py: en cada corrida saca una "foto" del sitio,
la compara con la anterior (state/cinemark.json) y devuelve los avisos.

Qué vigila:
- peliculas   películas nuevas, cambios de estado (próximamente → preventa → cartelera), salidas
- funciones   funciones nuevas, canceladas y cambios de horario en los 24 cines
- promos      promociones nuevas, que terminan o cambian
- merch       productos nuevos de la tienda, que se agotan/salen o cambian de precio

Todo sale de la API JSON bff.cinemark.com.ar, salvo el merch: la tienda
solo carga sus productos después de elegir un cine en el navegador, así que
se lee con Playwright y solo cuando se pide (incluir_merch=True), porque es
lo más lento.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.scrapers.cinemark import SITE, CinemarkScraper

from .showcase_imax import DIAS, TZ, Aviso, _e

SECCIONES = ("peliculas", "funciones", "promos", "merch")
MARGEN_CANCELADA = timedelta(hours=1)
CINE_MERCH = "Abasto"  # la tienda pide elegir un cine; el catálogo es el mismo en todos
MAX_LINEAS = 25        # para que un aviso no se vuelva interminable
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
        for cine in self.scraper._get("theaters", limit=1000):
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
                }
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
            if not p.get("active", True):
                continue
            items[str(p["id"])] = {
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
        avisos += _avisos_funciones(antes["funciones"], ahora["funciones"],
                                    ahora.get("peliculas") or antes["peliculas"], momento)
    if ambas("promos"):
        avisos += _avisos_promos(antes["promos"], ahora["promos"])
    if ambas("merch"):
        avisos += _avisos_merch(antes["merch"], ahora["merch"])
    return avisos


def _avisos_peliculas(antes: dict, ahora: dict, con_funciones: set[str]) -> list[Aviso]:
    avisos = []
    for k in ahora.keys() - antes.keys() - con_funciones:
        p = ahora[k]
        txt = f"🎬 <b>Película nueva en Cinemark: {_e(p['titulo'])}</b>\n{p['estado']}"
        if p["estreno"]:
            txt += f" · estreno {_dia(p['estreno'])}"
        if p["estado"] == "Preventa":
            txt += "\n🔥 ¡Ya está en preventa!"
        avisos.append(Aviso(txt, p.get("poster"), _boton("🎟️ Ver película y entradas", p["url"])))
    for k in antes.keys() & ahora.keys():
        a, p = antes[k], ahora[k]
        if a["estado"] != p["estado"]:
            fuego = "🔥 " if p["estado"] == "Preventa" else ""
            avisos.append(Aviso(f"📣 {fuego}<b>{_e(p['titulo'])}</b>: {a['estado']} → {p['estado']}",
                                p.get("poster"), _boton("🎟️ Comprar entradas", p["url"])))
        elif a["estreno"] != p["estreno"] and a["estreno"] and p["estreno"]:
            avisos.append(Aviso(f"📅 <b>{_e(p['titulo'])}</b>: cambió el estreno "
                                f"{_dia(a['estreno'])} → {_dia(p['estreno'])}",
                                botones=_boton("🎬 Ver película", p["url"])))
    for k in antes.keys() - ahora.keys():
        avisos.append(Aviso(f"👋 <b>Salió de Cinemark:</b> {_e(antes[k]['titulo'])}"))
    return avisos


def _avisos_funciones(antes: dict, ahora: dict, peliculas: dict, momento: datetime) -> list[Aviso]:
    avisos = []
    pelis_con_funciones_antes = {f["pelicula_id"] for f in antes.values()}

    # Funciones nuevas: un resumen por película.
    nuevas: dict[str, list[dict]] = {}
    for k in ahora.keys() - antes.keys():
        nuevas.setdefault(ahora[k]["pelicula_id"], []).append(ahora[k])
    if nuevas:
        primeras, resto = [], []
        for pid, fs in sorted(nuevas.items(), key=lambda x: -len(x[1])):
            titulo = fs[0]["pelicula"]
            url = (peliculas.get(pid) or {}).get("url", "")
            cines = sorted({f["cine"] for f in fs})
            rango = _rango(fs)
            if pid not in pelis_con_funciones_antes:
                primeras.append(Aviso(f"🔥 <b>¡Salieron las entradas de {_e(titulo)}!</b>\n"
                                      f"{len(fs)} funciones en {len(cines)} cines ({rango})\n"
                                      f"Cines: {_e(', '.join(cines))}",
                                      (peliculas.get(pid) or {}).get("poster"),
                                      _boton("🎟️ Comprar entradas", url)))
            else:
                resto.append(f"• <b>{_e(titulo)}</b>: +{len(fs)} en {len(cines)} cine{'s' if len(cines) != 1 else ''} ({rango})")
        avisos += primeras
        if resto:
            avisos.append(Aviso("🎟️ <b>Funciones nuevas en Cinemark</b>\n\n" + _recortar(resto)))

    # Funciones que desaparecen antes de empezar => canceladas.
    canceladas: dict[tuple[str, str], list[dict]] = {}
    for k in antes.keys() - ahora.keys():
        f = antes[k]
        if _inicio(f) > momento + MARGEN_CANCELADA:
            canceladas.setdefault((f["pelicula"], f["cine"]), []).append(f)
    if canceladas:
        lineas = [f"• <b>{_e(peli)}</b> — {_e(cine)}: "
                  + ", ".join(_cuando(f) for f in sorted(fs, key=_inicio)[:6])
                  + (f" y {len(fs) - 6} más" if len(fs) > 6 else "")
                  for (peli, cine), fs in sorted(canceladas.items())]
        avisos.append(Aviso("❌ <b>Funciones canceladas en Cinemark</b>\n\n" + _recortar(lineas)))

    # Misma función con otro horario.
    cambios = [f"• <b>{_e(ahora[k]['pelicula'])}</b> — {_e(ahora[k]['cine'])}: "
               f"{_cuando(antes[k])} → {_cuando(ahora[k])}"
               for k in antes.keys() & ahora.keys() if antes[k]["inicio"] != ahora[k]["inicio"]]
    if cambios:
        avisos.append(Aviso("🔄 <b>Cambios de horario en Cinemark</b>\n\n" + _recortar(sorted(cambios))))
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
    return Aviso("\n".join(partes))


# --------------------------------------------------------------------------- helpers

def _boton(texto: str, url: str) -> list[list[dict]]:
    return [[{"text": texto, "url": url}]] if url else []


def _inicio(f: dict) -> datetime:
    return datetime.strptime(f["inicio"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)


def _dia(fecha: str) -> str:
    d = datetime.strptime(fecha, "%Y-%m-%d")
    return f"{DIAS[d.weekday()]} {d:%d/%m}"


def _cuando(f: dict) -> str:
    return f"{_dia(f['inicio'][:10])} {f['inicio'][11:]}"


def _rango(fs: list[dict]) -> str:
    dias = sorted({f["inicio"][:10] for f in fs})
    return _dia(dias[0]) if len(dias) == 1 else f"{_dia(dias[0])} a {_dia(dias[-1])}"


def _pesos(n: int) -> str:
    return "$" + f"{n:,}".replace(",", ".")


def _recortar(lineas: list[str]) -> str:
    if len(lineas) <= MAX_LINEAS:
        return "\n".join(lineas)
    return "\n".join(lineas[:MAX_LINEAS]) + f"\n… y {len(lineas) - MAX_LINEAS} más"
