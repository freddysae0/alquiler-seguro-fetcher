import asyncio
import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
import fetcher
import calls

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
FAILURE_THRESHOLD = int(os.environ.get("FAILURE_THRESHOLD", "3"))

PROVINCIA, PRECIO_MIN, PRECIO_MAX, HABITACIONES, BANYOS = range(5)
SKIP = "-"

TW_ACCOUNT_SID, TW_AUTH_TOKEN, TW_FROM, TW_TO, TW_ENABLED = range(10, 15)

_consecutive_failures = 0


def format_config(config: dict) -> str:
    provincia = config.get("provincia") or "-"
    precio = []
    if config.get("precio_min") is not None:
        precio.append(f"min {config['precio_min']}")
    if config.get("precio_max") is not None:
        precio.append(f"max {config['precio_max']}")
    precio_s = " ".join(precio) if precio else "-"
    hab = config.get("habitaciones", "-") if config.get("habitaciones") is not None else "-"
    banyos = config.get("banyos", "-") if config.get("banyos") is not None else "-"
    return (
        f"Provincia: {provincia}\n"
        f"Precio: {precio_s}\n"
        f"Habitaciones: {hab}\n"
        f"Banos: {banyos}"
    )


def format_prop(prop: dict) -> str:
    url = f"https://www.alquilerseguro.es/viviendas/{prop['id']}"
    lines = [
        f"{prop.get('referencia') or 'Sin ref.'} - {prop.get('poblacion') or 'N/D'}",
    ]
    domicilio = prop.get("domicilio")
    if domicilio:
        cp = prop.get("codpostal")
        lines.append(f"{domicilio}" + (f" ({cp})" if cp else ""))
    precio = prop.get("precio")
    superficie = prop.get("superficie")
    hab = prop.get("habitaciones")
    banos = prop.get("banyos")
    det = []
    if precio is not None:
        det.append(f"Precio: {precio:.0f} EUR")
    if superficie is not None:
        det.append(f"Superficie: {superficie} m2")
    if hab is not None:
        det.append(f"Habitaciones: {hab}")
    if banos is not None:
        det.append(f"Banos: {banos}")
    if det:
        lines.append(" | ".join(det))
    lines.append(url)
    return "\n".join(lines)


def chunk_messages(blocks: list[str], limit: int = 4000) -> list[str]:
    messages = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > limit:
            messages.append(current)
            current = block
        elif current:
            current += "\n\n" + block
        else:
            current = block
    if current:
        messages.append(current)
    return messages


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    now = update.message.date.isoformat() if update.message.date else "now"
    db.upsert_user(user.id, update.effective_chat.id, user.username, now)
    await update.message.reply_text(
        "Hola. Comandos disponibles:\n"
        "/config - configura tus filtros de busqueda\n"
        "/get - busca ahora con tus filtros\n"
        "/twilio - configura Twilio para recibir llamadas\n"
        "/twilio_status - ver tu configuracion de Twilio\n"
        "/twilio_on - activar llamadas\n"
        "/twilio_off - desactivar llamadas\n"
        "Recibiras alertas automaticas cada vez que aparezca un inmueble nuevo que cumpla tus filtros."
    )


async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db.upsert_user(
        update.effective_user.id,
        update.effective_chat.id,
        update.effective_user.username,
        update.message.date.isoformat() if update.message.date else "now",
    )
    context.user_data["config"] = {}
    await update.message.reply_text(
        "Vamos a configurar tus filtros. Escribe la provincia (ej. BARCELONA) o '-' para omitir:"
    )
    return PROVINCIA


async def config_provincia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["config"]["provincia"] = None if text == SKIP else text.upper()
    await update.message.reply_text("Precio minimo (ej. 800) o '-' para omitir:")
    return PRECIO_MIN


async def config_precio_min(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["config"]["precio_min"] = None if text == SKIP else float(text)
    await update.message.reply_text("Precio maximo (ej. 1200) o '-' para omitir:")
    return PRECIO_MAX


async def config_precio_max(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["config"]["precio_max"] = None if text == SKIP else float(text)
    await update.message.reply_text("Numero minimo de habitaciones o '-' para omitir:")
    return HABITACIONES


async def config_habitaciones(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["config"]["habitaciones"] = None if text == SKIP else int(text)
    await update.message.reply_text("Numero minimo de banos o '-' para omitir:")
    return BANYOS


async def config_banyos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    config = context.user_data["config"]
    config["banyos"] = None if text == SKIP else int(text)
    user_id = update.effective_user.id

    try:
        properties = fetcher.search(config)
    except Exception as exc:
        logger.exception("Error buscando al guardar config")
        await update.message.reply_text("No he podido consultar la web. Config no guardada.")
        return ConversationHandler.END

    db.save_config(user_id, config)
    db.reset_notified(user_id)
    db.add_notified(user_id, [p["id"] for p in properties])

    await update.message.reply_text(
        "Config guardada:\n" + format_config(config) + "\n\nAhora mismo hay "
        f"{len(properties)} inmuebles que cumplen tus filtros. Te avisare cuando aparezcan nuevos."
    )
    return ConversationHandler.END


async def config_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Configuracion cancelada.")
    return ConversationHandler.END


async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    config = db.get_config(user_id)
    if not config:
        await update.message.reply_text("Aun no has configurado filtros. Usa /config primero.")
        return

    await update.message.reply_text("Buscando...")
    try:
        properties = fetcher.search(config)
    except Exception as exc:
        logger.exception("Error en /get")
        await update.message.reply_text("Error al consultar la web. Intentalo de nuevo.")
        return

    if not properties:
        await update.message.reply_text("No hay inmuebles que cumplan tus filtros.")
        return

    blocks = [format_prop(p) for p in properties]
    for msg in chunk_messages(blocks):
        await update.message.reply_text(msg)


def _mask(value: str | None) -> str:
    if not value:
        return "-"
    if len(value) <= 4:
        return "****"
    return value[:4] + "****" + value[-4:]


def format_twilio_status(twilio: dict | None) -> str:
    if not twilio:
        return "Twilio no configurado. Usa /twilio para configurarlo."
    estado = "activadas" if twilio.get("enabled") else "desactivadas"
    return (
        "Twilio configurado:\n"
        f"Account SID: {_mask(twilio.get('account_sid'))}\n"
        f"Auth Token: {_mask(twilio.get('auth_token'))}\n"
        f"Desde (from): {twilio.get('from_number') or '-'}\n"
        f"Hacia (to): {twilio.get('to_number') or '-'}\n"
        f"Llamadas: {estado}"
    )


async def twilio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db.upsert_user(
        update.effective_user.id,
        update.effective_chat.id,
        update.effective_user.username,
        update.message.date.isoformat() if update.message.date else "now",
    )
    context.user_data["twilio"] = {}
    await update.message.reply_text(
        "Vamos a configurar Twilio. Escribe tu Account SID:"
    )
    return TW_ACCOUNT_SID


async def twilio_account_sid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["twilio"]["account_sid"] = update.message.text.strip()
    await update.message.reply_text("Escribe tu Auth Token:")
    return TW_AUTH_TOKEN


async def twilio_auth_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["twilio"]["auth_token"] = update.message.text.strip()
    await update.message.reply_text(
        "Numero de Twilio que realiza la llamada (from), ej. +34XXXXXXXXX:"
    )
    return TW_FROM


async def twilio_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["twilio"]["from_number"] = update.message.text.strip()
    await update.message.reply_text("Numero al que llamar (to), ej. +34XXXXXXXXX:")
    return TW_TO


async def twilio_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["twilio"]["to_number"] = update.message.text.strip()
    await update.message.reply_text("Quieres recibir llamadas? (si/no):")
    return TW_ENABLED


async def twilio_enabled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()
    twilio = context.user_data["twilio"]
    twilio["enabled"] = text in ("si", "s", "yes", "y", "1", "true")

    db.save_twilio(update.effective_user.id, twilio)
    await update.message.reply_text("Twilio guardado.\n" + format_twilio_status(twilio))
    return ConversationHandler.END


async def twilio_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Configuracion de Twilio cancelada.")
    return ConversationHandler.END


async def twilio_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    twilio = db.get_twilio(update.effective_user.id)
    await update.message.reply_text(format_twilio_status(twilio))


async def _set_twilio_enabled(update: Update, enabled: bool) -> None:
    user_id = update.effective_user.id
    twilio = db.get_twilio(user_id)
    if not twilio or not all(twilio.get(k) for k in ("account_sid", "auth_token", "from_number", "to_number")):
        await update.message.reply_text("Primero configura Twilio con /twilio.")
        return
    db.set_twilio_enabled(user_id, enabled)
    await update.message.reply_text(
        "Llamadas " + ("activadas." if enabled else "desactivadas.")
    )


async def twilio_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_twilio_enabled(update, True)


async def twilio_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_twilio_enabled(update, False)


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _consecutive_failures
    any_error = False
    for cfg in db.all_configs():
        user_id = cfg.pop("user_id")
        chat_id = cfg.pop("chat_id")
        try:
            properties = fetcher.search(cfg)
        except Exception:
            logger.exception("Error en alerta para user %s", user_id)
            any_error = True
            continue

        new_props = [p for p in properties if not db.has_notified(user_id, p["id"])]
        if new_props:
            db.add_notified(user_id, [p["id"] for p in new_props])
            blocks = [format_prop(p) for p in new_props]
            for msg in chunk_messages(blocks):
                try:
                    await context.bot.send_message(chat_id, "Nuevo inmueble:\n\n" + msg)
                except Exception:
                    logger.exception("Error enviando alerta a %s", chat_id)

            twilio = db.get_twilio(user_id)
            if twilio and twilio.get("enabled"):
                for prop in new_props:
                    try:
                        await asyncio.to_thread(calls.make_call, twilio, prop)
                    except Exception:
                        logger.exception("Error llamando a user %s por %s", user_id, prop.get("id"))

    if any_error:
        _consecutive_failures += 1
        if _consecutive_failures >= FAILURE_THRESHOLD and ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    ADMIN_CHAT_ID,
                    f"El fetch de alquilerseguro.es ha fallado {_consecutive_failures} veces "
                    "seguidas. Revisa los logs del contenedor.",
                )
                _consecutive_failures = 0
            except Exception:
                logger.exception("Error enviando alerta de fallo al admin")
    else:
        _consecutive_failures = 0


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Falta BOT_TOKEN. Copia .env.example a .env y configura el token.")

    db.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("get", get_cmd))
    application.add_handler(CommandHandler("twilio_status", twilio_status_cmd))
    application.add_handler(CommandHandler("twilio_on", twilio_on_cmd))
    application.add_handler(CommandHandler("twilio_off", twilio_off_cmd))
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("config", config_cmd)],
            states={
                PROVINCIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, config_provincia)],
                PRECIO_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, config_precio_min)],
                PRECIO_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, config_precio_max)],
                HABITACIONES: [MessageHandler(filters.TEXT & ~filters.COMMAND, config_habitaciones)],
                BANYOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, config_banyos)],
            },
            fallbacks=[CommandHandler("cancel", config_cancel)],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("twilio", twilio_cmd)],
            states={
                TW_ACCOUNT_SID: [MessageHandler(filters.TEXT & ~filters.COMMAND, twilio_account_sid)],
                TW_AUTH_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, twilio_auth_token)],
                TW_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, twilio_from)],
                TW_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, twilio_to)],
                TW_ENABLED: [MessageHandler(filters.TEXT & ~filters.COMMAND, twilio_enabled)],
            },
            fallbacks=[CommandHandler("cancel", twilio_cancel)],
        )
    )

    application.job_queue.run_repeating(
        check_alerts, interval=POLL_INTERVAL, first=POLL_INTERVAL
    )

    logger.info("Bot arrancado. Intervalo de alertas: %ss", POLL_INTERVAL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
