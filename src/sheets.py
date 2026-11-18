"""Escritura de los datos scrapeados en Google Sheets.

Cada tabla (Peliculas, Cines, Funciones) va a una pestaña con ese nombre.
Al escribir, se reemplazan solo las filas de la cadena que se acaba de
scrapear, así varias cadenas de cine pueden convivir en la misma pestaña.
"""

from __future__ import annotations

import gspread
import pandas as pd
from gspread_dataframe import get_as_dataframe, set_with_dataframe

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def abrir_planilla(credenciales: str, spreadsheet_id: str) -> gspread.Spreadsheet:
    gc = gspread.service_account(filename=credenciales, scopes=SCOPES)
    return gc.open_by_key(spreadsheet_id)


def _pestania(sh: gspread.Spreadsheet, nombre: str) -> gspread.Worksheet:
    try:
        return sh.worksheet(nombre)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=nombre, rows=100, cols=20)


def guardar(sh: gspread.Spreadsheet, nombre: str, filas: list[dict], cadena: str) -> int:
    ws = _pestania(sh, nombre)
    nuevo = pd.DataFrame(filas)

    existente = get_as_dataframe(ws, dtype=str).dropna(how="all").dropna(axis=1, how="all")
    if "cadena" in existente.columns:
        existente = existente[existente["cadena"] != cadena]
        df = pd.concat([existente, nuevo], ignore_index=True)
    else:
        df = nuevo

    ws.clear()
    ws.resize(rows=max(len(df) + 1, 2), cols=max(len(df.columns), 1))
    set_with_dataframe(ws, df, include_index=False, resize=False)
    ws.freeze(rows=1)
    return len(nuevo)
