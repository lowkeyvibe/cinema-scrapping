"""Corre los monitores de novedades y manda los avisos por Telegram.

Uso:
    python -m src.monitor                      # todos los monitores
    python -m src.monitor cinemark             # solo uno (cinemark, showcase_imax)
    python -m src.monitor --merch              # además revisa la tienda de Cinemark (usa Playwright)
    python -m src.monitor --dry-run            # muestra los avisos en pantalla, no manda nada ni guarda

Cada monitor guarda su foto en state/<monitor>.json. La primera vez no hay con
qué comparar: se guarda la foto y se manda un mensaje de "monitor activado".
Si un monitor falla, los demás siguen y el comando termina con error.

Cada monitor puede tener su propio bot y chat de Telegram:
    TELEGRAM_BOT_TOKEN_CINEMARK / TELEGRAM_CHAT_ID_CINEMARK
    TELEGRAM_BOT_TOKEN_SHOWCASE_IMAX / TELEGRAM_CHAT_ID_SHOWCASE_IMAX
Si no están, usa TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

from . import cinemark, showcase_imax
from .showcase_imax import Aviso
from .telegram import Telegram

MONITORES = {
    "showcase_imax": showcase_imax,
    "cinemark": cinemark,
}
NOMBRES = {"showcase_imax": "Showcase IMAX", "cinemark": "Cinemark"}
FALLAS_PARA_AVISAR = 6  # corridas seguidas con error en una sección (= 3 h si corre cada 30 min)


def telegram_de(clave: str) -> Telegram:
    """El bot/chat propio del monitor, o el general si no tiene uno."""
    sufijo = clave.upper()
    return Telegram(token=os.environ.get(f"TELEGRAM_BOT_TOKEN_{sufijo}") or None,
                    chat_id=os.environ.get(f"TELEGRAM_CHAT_ID_{sufijo}") or None)


def correr(clave: str, tg: Telegram, dry_run: bool, merch: bool) -> None:
    modulo = MONITORES[clave]
    nombre = NOMBRES[clave]
    estado = Path(f"state/{clave}.json")

    print(f"{nombre}: sacando foto del sitio…")
    fotografo = modulo.Fotografo(incluir_merch=merch) if clave == "cinemark" else modulo.Fotografo()
    ahora = fotografo.sacar()
    if not ahora:
        raise RuntimeError("no se pudo leer ninguna sección; no se cambia nada")

    antes = json.loads(estado.read_text()) if estado.exists() else None
    avisos = [modulo.resumen_inicial(ahora)] if antes is None else modulo.comparar(antes, ahora)

    # Contar fallas seguidas por sección y avisar una sola vez si se vuelven persistentes.
    # Solo cuentan las secciones que esta corrida intentó leer (el merch no se lee siempre).
    fallas = dict((antes or {}).get("_fallas", {}))
    for seccion in getattr(fotografo, "intentadas", modulo.SECCIONES):
        fallas[seccion] = 0 if seccion in ahora else fallas.get(seccion, 0) + 1
        if fallas[seccion] == FALLAS_PARA_AVISAR:
            avisos.append(Aviso(f"⚠️ <b>Monitor {nombre}</b>: no puedo leer «{seccion}» hace "
                                f"{FALLAS_PARA_AVISAR} corridas. Puede que el sitio haya cambiado."))
    ahora["_fallas"] = fallas
    print(f"  {len(avisos)} aviso(s)")

    for a in avisos:
        if dry_run:
            botones = " | ".join(f"[{b['text']}]" for fila in a.botones for b in fila)
            print("-" * 60 + (f"\n[imagen] {a.imagen}" if a.imagen else "") + "\n" + a.texto
                  + (f"\n{botones}" if botones else ""))
        else:
            tg.enviar(a)

    if not dry_run:
        # Las secciones que fallaron (o no se leyeron) conservan la foto anterior.
        nuevo = {**(antes or {}), **ahora}
        estado.parent.mkdir(exist_ok=True)
        estado.write_text(json.dumps(nuevo, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("monitores", nargs="*", metavar="monitor",
                        help=f"qué monitores correr: {', '.join(MONITORES)} (default: todos)")
    parser.add_argument("--merch", action="store_true", help="revisar también la tienda de Cinemark (Playwright)")
    parser.add_argument("--dry-run", action="store_true", help="no mandar a Telegram ni guardar la foto")
    args = parser.parse_args()
    if desconocidos := set(args.monitores) - set(MONITORES):
        parser.error(f"monitor desconocido: {', '.join(desconocidos)}")
    load_dotenv()

    claves = args.monitores or list(MONITORES)
    bots = {clave: telegram_de(clave) for clave in claves}
    if not args.dry_run and (sin_bot := [c for c, tg in bots.items() if not tg.configurado]):
        print(f"Falta el bot de Telegram para: {', '.join(sin_bot)} (TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID; "
              "usá --dry-run para probar).", file=sys.stderr)
        return 1

    fallaron = []
    for clave in claves:
        t0 = time.monotonic()
        try:
            correr(clave, bots[clave], args.dry_run, args.merch)
        except Exception:  # noqa: BLE001 - un monitor roto no frena a los demás
            traceback.print_exc()
            fallaron.append(clave)
        print(f"  ({time.monotonic() - t0:.1f} s)")

    if fallaron:
        print(f"Fallaron: {', '.join(fallaron)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
