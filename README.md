# cinema-scrapping

App para scrapear páginas de cine y cargar los datos en Google Sheets.

## Setup

```bash
python3.12 -m venv .venv   # requiere Python 3.12+
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # completar con tus datos
```

## Librerías

| Uso | Librerías |
|---|---|
| Peticiones HTTP | requests, httpx, fake-useragent, tenacity |
| Parseo HTML | beautifulsoup4, lxml |
| Sitios con JavaScript | playwright |
| Google Sheets | gspread, google-auth, gspread-dataframe |
| Datos y utilidades | pandas, python-dotenv, schedule |
