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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))

PROVINCIA, PRECIO_MIN, PRECIO_MAX, HABITACIONES, BANYOS = range(5)
SKIP = "-"


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


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    for cfg in db.all_configs():
        user_id = cfg.pop("user_id")
        chat_id = cfg.pop("chat_id")
        try:
            properties = fetcher.search(cfg)
        except Exception:
            logger.exception("Error en alerta para user %s", user_id)
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


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Falta BOT_TOKEN. Copia .env.example a .env y configura el token.")

    db.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("get", get_cmd))
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

    application.job_queue.run_repeating(
        check_alerts, interval=POLL_INTERVAL, first=POLL_INTERVAL
    )

    logger.info("Bot arrancado. Intervalo de alertas: %ss", POLL_INTERVAL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
