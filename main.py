"""Scrapea los sitios de cine y guarda los datos en Google Sheets.

Uso:
    python main.py            # scrapea y escribe en Google Sheets
    python main.py --csv      # scrapea y guarda CSV en output/ (no toca Sheets)
"""

import argparse
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src.scrapers.cinemark import CADENA, CinemarkScraper


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", action="store_true", help="guardar en CSV en vez de Google Sheets")
    args = parser.parse_args()

    tablas = CinemarkScraper().scrapear()

    if args.csv:
        out = Path("output")
        out.mkdir(exist_ok=True)
        for nombre, filas in tablas.items():
            ruta = out / f"{CADENA.lower()}_{nombre.lower()}.csv"
            pd.DataFrame(filas).to_csv(ruta, index=False)
            print(f"{nombre}: {len(filas)} filas → {ruta}")
        return

    load_dotenv()
    from src.sheets import abrir_planilla, guardar

    sh = abrir_planilla(os.environ["GOOGLE_CREDENTIALS_FILE"], os.environ["SPREADSHEET_ID"])
    for nombre, filas in tablas.items():
        n = guardar(sh, nombre, filas, CADENA)
        print(f"{nombre}: {n} filas escritas en Google Sheets")
    print(f"Listo: {sh.url}")


if __name__ == "__main__":
    main()
