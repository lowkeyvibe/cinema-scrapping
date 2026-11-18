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

## Uso

```bash
python main.py --csv   # prueba: guarda CSV en output/
python main.py         # escribe en Google Sheets (requiere .env y la service account)
```

Se generan tres pestañas en la planilla: **Peliculas**, **Cines** y **Funciones**.
Cada corrida reemplaza las filas de esa cadena, así que otras cadenas pueden convivir en las mismas pestañas.

## Sitios soportados

| Cadena | Fuente |
|---|---|
| Cinemark Argentina | API JSON `bff.cinemark.com.ar` (requiere encabezado `country: AR`) |

## Conectar Google Sheets

1. En [Google Cloud Console](https://console.cloud.google.com/) creá un proyecto y habilitá **Google Sheets API**.
2. En *IAM y administración → Cuentas de servicio*, creá una cuenta y generá una clave **JSON**.
3. Guardá el archivo como `service_account.json` en la raíz del proyecto (está en `.gitignore`).
4. Compartí la planilla con el email de la cuenta de servicio (`...@...iam.gserviceaccount.com`) como **Editor**.
