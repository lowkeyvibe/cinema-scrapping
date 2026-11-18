"""Scrapea los sitios de cine y guarda los datos en Google Sheets.

Uso:
    python main.py                          # todas las cadenas → Google Sheets
    python main.py --csv                    # todas las cadenas → CSV en output/
    python main.py showcase_imax --csv      # solo una cadena
"""

import argparse
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src.scrapers import cinemark, showcase_imax

SCRAPERS = {
    "cinemark": (cinemark.CADENA, cinemark.CinemarkScraper),
    "showcase_imax": (showcase_imax.CADENA, showcase_imax.ShowcaseImaxScraper),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cadenas", nargs="*", metavar="cadena",
                        help=f"qué scrapers correr: {', '.join(SCRAPERS)} (default: todos)")
    parser.add_argument("--csv", action="store_true", help="guardar en CSV en vez de Google Sheets")
    args = parser.parse_args()
    if desconocidas := set(args.cadenas) - set(SCRAPERS):
        parser.error(f"cadena desconocida: {', '.join(desconocidas)}")

    sh = None
    if not args.csv:
        load_dotenv()
        from src.sheets import abrir_planilla
        sh = abrir_planilla(os.environ["GOOGLE_CREDENTIALS_FILE"], os.environ["SPREADSHEET_ID"])

    for clave in args.cadenas or SCRAPERS:
        cadena, Scraper = SCRAPERS[clave]
        tablas = Scraper().scrapear()

        if args.csv:
            out = Path("output")
            out.mkdir(exist_ok=True)
            for nombre, filas in tablas.items():
                ruta = out / f"{clave}_{nombre.lower()}.csv"
                pd.DataFrame(filas).to_csv(ruta, index=False)
                print(f"{nombre}: {len(filas)} filas → {ruta}")
            continue

        from src.sheets import guardar
        for nombre, filas in tablas.items():
            n = guardar(sh, nombre, filas, cadena)
            print(f"{nombre}: {n} filas escritas en Google Sheets")

    if sh is not None:
        print(f"Listo: {sh.url}")


if __name__ == "__main__":
    main()
