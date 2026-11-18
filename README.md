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
python main.py --csv                 # prueba: guarda CSV en output/
python main.py                       # escribe en Google Sheets (requiere .env y la service account)
python main.py showcase_imax --csv   # solo una cadena (cinemark, showcase_imax)
```

Se generan tres pestañas en la planilla: **Peliculas**, **Cines** y **Funciones**.
Cada corrida reemplaza las filas de esa cadena, así que otras cadenas pueden convivir en las mismas pestañas.

## Sitios soportados

| Cadena | Fuente |
|---|---|
| Cinemark Argentina | API JSON `bff.cinemark.com.ar` (requiere encabezado `country: AR`) |
| Showcase — solo IMAX Theatre (Norcenter) | Películas del bloque IMAX de la home de todoshowcase.com + horarios de la API JSON `api.voyalcine.net/films/{id}/tree/3250` |

## Conectar Google Sheets

1. En [Google Cloud Console](https://console.cloud.google.com/) creá un proyecto y habilitá **Google Sheets API**.
2. En *IAM y administración → Cuentas de servicio*, creá una cuenta y generá una clave **JSON**.
3. Guardá el archivo como `service_account.json` en la raíz del proyecto (está en `.gitignore`).
4. Compartí la planilla con el email de la cuenta de servicio (`...@...iam.gserviceaccount.com`) como **Editor**.

## Monitor de novedades (avisos por Telegram)

`python -m src.monitor` saca una "foto" de cada sitio, la compara con la anterior
(`state/<monitor>.json`) y manda por Telegram lo que cambió. Monitores: `showcase_imax` y `cinemark`.

### Showcase IMAX

| Aviso | Fuente |
|---|---|
| 🎬 Película nueva en IMAX / 🔥 arranque de preventa | cartelera IMAX de la home |
| 🎟️ Funciones nuevas · ❌ canceladas · 🔄 cambio de horario | API `api.voyalcine.net` |
| 🏷️ Promos IMAX nuevas, que terminan o cambian de vigencia | `/promocion` |
| 🎁 Sorteos nuevos (kits de merch) | `/sorteo` |
| 🖼️ Banners nuevos en la home (combos, merch, eventos) | carrusel de la home |
| 🍿 Cambios en Candy | `/candy` |
| 💲 Cambios de precio IMAX | `cron/data.js` |

### Cinemark

| Aviso | Fuente |
|---|---|
| 🎬 Película nueva · 📣 cambio de estado (🔥 arranque de preventa) · 📅 cambio de estreno · 👋 sale de cartelera | API `bff.cinemark.com.ar/api/cinema/movies` |
| 🔥 ¡Salieron las entradas! (primera vez que una película tiene funciones) | API `.../showtimes?theater=` (24 cines) |
| 🎟️ Funciones nuevas (resumen por película) · ❌ canceladas · 🔄 cambio de horario | ídem |
| 🏷️ Promos nuevas, que terminan o cambian | API `.../content/promotions` |
| 🛍️ Merch nuevo, que sale de la tienda o 💲 cambia de precio | tienda `/store` con Playwright (solo con `--merch`, cada 6 h) |

Si una sección no se puede leer, se conserva su foto anterior (sin falsos avisos); si falla
6 corridas seguidas, llega un aviso ⚠️.

```bash
python -m src.monitor --dry-run            # muestra los avisos en pantalla, sin mandar ni guardar
python -m src.monitor                      # todos los monitores (requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID)
python -m src.monitor cinemark --merch     # solo Cinemark, incluyendo la tienda de merch
```

### Configurar Telegram

1. En Telegram, hablale a **@BotFather** → `/newbot` → copiá el token.
2. Mandale cualquier mensaje a tu bot nuevo.
3. `TELEGRAM_BOT_TOKEN=<token> python -m src.monitor.telegram` → te imprime tu `TELEGRAM_CHAT_ID`.

### Correrlo en GitHub Actions

El workflow [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml) corre todos los
monitores cada 30 minutos (y el merch de Cinemark cada 6 h) y guarda las fotos en `state/` con un commit.
Si un monitor falla, los demás siguen igual y la corrida queda en rojo.

1. En el repo: *Settings → Secrets and variables → Actions* → agregar `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`
   (o `gh secret set TELEGRAM_BOT_TOKEN` / `gh secret set TELEGRAM_CHAT_ID`).
2. *Actions → Monitor de cines → Run workflow* para la primera corrida (manda un "monitor activado" por cada sitio).

Con el repo privado: las corridas cada 30 min duran menos de 1 minuto facturado (~1.440 min/mes) y las del
merch, ~2 min cada 6 h (~240 min/mes), así que entra en los 2.000 minutos gratis por mes de Actions.
