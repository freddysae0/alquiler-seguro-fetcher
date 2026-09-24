from __future__ import annotations

import logging

from twilio.rest import Client

logger = logging.getLogger(__name__)


def _twiml_for(prop: dict) -> str:
    referencia = prop.get("referencia") or "desconocida"
    poblacion = prop.get("poblacion") or ""
    precio = prop.get("precio")
    precio_s = f"{precio:.0f} euros" if precio is not None else ""
    texto = (
        f"Hola, tienes un nuevo inmueble en Alquiler Seguro. Referencia {referencia} "
        f"en {poblacion}. {precio_s}. Consulta el enlace en Telegram."
    )
    return (
        "<Response>"
        f"<Say language='es-ES' voice='Polly.Lucia'>{texto}</Say>"
        "</Response>"
    )


def make_call(twilio: dict, prop: dict) -> None:
    required = ("account_sid", "auth_token", "from_number", "to_number")
    if not all(twilio.get(k) for k in required):
        logger.warning("Twilio config incompleta, no se llama")
        return

    client = Client(twilio["account_sid"], twilio["auth_token"])
    client.calls.create(
        to=twilio["to_number"],
        from_=twilio["from_number"],
        twiml=_twiml_for(prop),
    )
