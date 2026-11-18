"""Envío de mensajes por Telegram (Bot API).

Necesita dos variables de entorno:
    TELEGRAM_BOT_TOKEN   el token que te da @BotFather
    TELEGRAM_CHAT_ID     tu chat (o el de un grupo/canal)

Para averiguar el chat id: mandale cualquier mensaje a tu bot y corré
    python -m src.monitor.telegram
"""

from __future__ import annotations

import json
import os
import sys

import requests

API = "https://api.telegram.org/bot{token}/{method}"
LIMITE_TEXTO = 4000     # Telegram corta en 4096
LIMITE_CAPTION = 1000   # y en 1024 para el texto de una foto


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    @property
    def configurado(self) -> bool:
        return bool(self.token and self.chat_id)

    def _llamar(self, metodo: str, **datos) -> dict:
        r = requests.post(API.format(token=self.token, method=metodo), data=datos, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Telegram {metodo} falló ({r.status_code}): {r.text[:300]}")
        return r.json()

    def enviar(self, aviso) -> None:
        """Manda un Aviso (texto, imagen opcional y botones opcionales)."""
        botones = getattr(aviso, "botones", None)
        if aviso.imagen:
            self.foto(aviso.imagen, aviso.texto, botones)
        else:
            self.mensaje(aviso.texto, botones)

    def mensaje(self, html: str, botones: list[list[dict]] | None = None) -> None:
        partes = _partir(html, LIMITE_TEXTO)
        for i, parte in enumerate(partes):
            extra = _teclado(botones) if i == len(partes) - 1 else {}
            self._llamar("sendMessage", chat_id=self.chat_id, text=parte,
                         parse_mode="HTML", disable_web_page_preview="true", **extra)

    def foto(self, url: str, html: str, botones: list[list[dict]] | None = None) -> None:
        if len(html) > LIMITE_CAPTION:
            # El texto no entra como epígrafe: foto sola y después el texto completo con los botones.
            try:
                self._llamar("sendPhoto", chat_id=self.chat_id, photo=url)
            except RuntimeError:
                pass
            self.mensaje(html, botones)
            return
        try:
            self._llamar("sendPhoto", chat_id=self.chat_id, photo=url,
                         caption=html, parse_mode="HTML", **_teclado(botones))
        except RuntimeError:
            # Si Telegram no puede bajar la imagen, mandamos solo el texto.
            self.mensaje(html, botones)


def _teclado(botones: list[list[dict]] | None) -> dict:
    return {"reply_markup": json.dumps({"inline_keyboard": botones})} if botones else {}


def _partir(texto: str, limite: int) -> list[str]:
    """Corta en bloques de líneas completas para no romper el HTML."""
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if actual and len(actual) + len(linea) + 1 > limite:
            partes.append(actual)
            actual = ""
        actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        partes.append(actual)
    return partes


if __name__ == "__main__":
    # Ayuda para obtener el chat id: lista los chats que le escribieron al bot.
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not token:
        sys.exit("Pasá el token: TELEGRAM_BOT_TOKEN=... python -m src.monitor.telegram")
    updates = requests.get(API.format(token=token, method="getUpdates"), timeout=30).json()
    chats = {}
    for u in updates.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        if chat := msg.get("chat"):
            chats[chat["id"]] = chat.get("title") or chat.get("first_name") or chat.get("username")
    if not chats:
        print("No hay mensajes todavía: mandale un mensaje a tu bot y volvé a correr esto.")
    for cid, nombre in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({nombre})")
