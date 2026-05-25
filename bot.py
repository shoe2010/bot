import logging
import os
import random
import re
import asyncio
import json

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8794814572:AAGTaCtmt19aqb5Dap7T6LGoRB732Rb21yM"

BANKVALIDOR_URL = os.getenv(
    "BANKVALIDOR_URL", "https://bankvalidor.com/api/v1/validate/us"
)
BANKVALIDOR_ORIGIN = os.getenv("BANKVALIDOR_ORIGIN", "https://bankvalidor.com")
BANKVALIDOR_REFERER = os.getenv("BANKVALIDOR_REFERER", "https://bankvalidor.com/es")
ACCOUNT_PLACEHOLDER = os.getenv("BANKVALIDOR_ACCOUNT_PLACEHOLDER", "0000000000")
ADMIN_IDS = {6279771747, 8636130500, 5588303737, 8244977058}

ROUTING_RE = re.compile(r"^\d{9}$")
PREFIJOS_OBSERVADOS = [237, 428, 437, 441]
RUTA_EJEMPLO = "011103093"

ARCHIVO_JSON = os.path.join(os.path.dirname(__file__), "cuentas_generadas.json")
ARCHIVO_USUARIOS = os.path.join(os.path.dirname(__file__), "usuarios_autorizados.json")


# --- CUENTAS GENERADAS ---


def cargar_cuentas_generadas() -> set:
    if os.path.exists(ARCHIVO_JSON):
        try:
            with open(ARCHIVO_JSON, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            logger.error("Error al leer cuentas_generadas.json.")
            return set()
    return set()


def guardar_cuentas_generadas(cuentas: set):
    try:
        with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
            json.dump(list(cuentas), f, indent=4)
    except Exception:
        logger.error("Error al guardar cuentas_generadas.json.")


cuentas_historicas = cargar_cuentas_generadas()


# --- USUARIOS AUTORIZADOS ---


def cargar_usuarios_autorizados() -> set:
    if os.path.exists(ARCHIVO_USUARIOS):
        try:
            with open(ARCHIVO_USUARIOS, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            logger.error("Error al leer usuarios_autorizados.json.")
            return set()
    return set()


def guardar_usuarios_autorizados(usuarios: set):
    try:
        with open(ARCHIVO_USUARIOS, "w", encoding="utf-8") as f:
            json.dump(list(usuarios), f, indent=4)
    except Exception:
        logger.error("Error al guardar usuarios_autorizados.json.")


usuarios_autorizados = cargar_usuarios_autorizados()


# --- PERMISOS ---


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and user.id in ADMIN_IDS


def tiene_acceso(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and (user.id in ADMIN_IDS or user.id in usuarios_autorizados)


# --- COMANDOS DE ACCESO ---


async def add_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("No autorizado.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /id <user_id>")
        return

    try:
        nuevo_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("El ID debe ser numérico.")
        return

    if nuevo_id in ADMIN_IDS:
        await update.message.reply_text("Ese ID ya es admin.")
        return

    if nuevo_id in usuarios_autorizados:
        await update.message.reply_text(f"✅ {nuevo_id} ya tenía acceso.")
        return

    usuarios_autorizados.add(nuevo_id)
    guardar_usuarios_autorizados(usuarios_autorizados)
    await update.message.reply_text(f"✅ Acceso otorgado a {nuevo_id}.")


async def remove_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("No autorizado.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /exid <user_id>")
        return

    try:
        target_id = int(context.args[0].strip())
    except ValueError:
        await update.message.reply_text("El ID debe ser numérico.")
        return

    if target_id in ADMIN_IDS:
        await update.message.reply_text("No se puede quitar acceso a un admin.")
        return

    if target_id not in usuarios_autorizados:
        await update.message.reply_text(f"❌ {target_id} no tenía acceso.")
        return

    usuarios_autorizados.discard(target_id)
    guardar_usuarios_autorizados(usuarios_autorizados)
    await update.message.reply_text(f"✅ Acceso removido a {target_id}.")


# --- LÓGICA ---


def aba_checksum_ok(routing_number: str) -> bool:
    digits = [int(ch) for ch in routing_number]
    total = (
        3 * (digits[0] + digits[3] + digits[6])
        + 7 * (digits[1] + digits[4] + digits[7])
        + 1 * (digits[2] + digits[5] + digits[8])
    )
    return total % 10 == 0


def verify_routing_remote(routing_number: str) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": BANKVALIDOR_ORIGIN,
        "Referer": BANKVALIDOR_REFERER,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }
    payload = {
        "routing_number": routing_number,
        "account_number": ACCOUNT_PLACEHOLDER,
        "bic": "",
    }
    response = requests.post(BANKVALIDOR_URL, json=payload, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def generar_numero_similar() -> int:
    prefijo = random.choice(PREFIJOS_OBSERVADOS) + random.randint(-10, 10)
    prefijo = max(100, min(999, prefijo))
    resto = random.randint(1000000, 9999999)
    return int(f"{prefijo}{resto}")


def calcular_luhn(numero_base_9: int) -> int:
    digitos = [int(d) for d in str(numero_base_9)][::-1]
    suma = 0
    for i, d in enumerate(digitos):
        if i % 2 == 0:
            d2 = d * 2
            if d2 > 9:
                d2 -= 9
            suma += d2
        else:
            suma += d
    return (10 - (suma % 10)) % 10


def generar_cuenta_completa() -> int:
    prefijo_9 = generar_numero_similar() // 10
    digito = calcular_luhn(prefijo_9)
    return prefijo_9 * 10 + digito


# --- HANDLERS ---


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "Bienvenido.\n"
        "Comandos:\n"
        "- /ruta <routing_number_9_digitos>\n"
        "- /td <cantidad>\n\n"
        "Solo admins:\n"
        "- /id <user_id> — otorgar acceso\n"
        "- /exid <user_id> — quitar acceso"
    )
    await update.message.reply_text(msg)


async def ruta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not tiene_acceso(update):
        await update.message.reply_text("No autorizado.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /ruta <numero_9_digitos>")
        return

    routing_number = context.args[0].strip()

    if not ROUTING_RE.match(routing_number):
        await update.message.reply_text("Debe tener 9 digitos numericos.")
        return

    checksum_ok = aba_checksum_ok(routing_number)

    try:
        data = verify_routing_remote(routing_number)
        status = data.get("status", "N/A")

        if status == "valid" or (checksum_ok and status != "invalid"):
            mensaje = f"✅ {routing_number} - Válida"
        else:
            mensaje = f"❌ {routing_number} - No válida"

        await update.message.reply_text(mensaje)

    except Exception:
        logger.exception("Error en /ruta")
        if checksum_ok:
            await update.message.reply_text(
                f"✅ {routing_number} - Válida (verificación local)"
            )
        else:
            await update.message.reply_text(f"❌ {routing_number} - No válida")


async def td(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not tiene_acceso(update):
        await update.message.reply_text("No autorizado.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /td <cantidad>")
        return

    try:
        qty = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Debe ser un numero.")
        return

    if qty < 1 or qty > 100:
        await update.message.reply_text("Cantidad entre 1 y 100.")
        return

    waiting_msg = await update.message.reply_text("Esperando...")

    delay = random.randint(5, 10)
    await asyncio.sleep(delay)

    resultados = []
    hubo_nuevas_cuentas = False

    for _ in range(qty):
        cuenta = generar_cuenta_completa()
        cuenta_str = str(cuenta)

        while cuenta_str in cuentas_historicas:
            cuenta = generar_cuenta_completa()
            cuenta_str = str(cuenta)

        cuentas_historicas.add(cuenta_str)
        hubo_nuevas_cuentas = True
        resultados.append(f"TD BANK\nRUTA: {RUTA_EJEMPLO}\nCUENTA: {cuenta}")

    if hubo_nuevas_cuentas:
        guardar_cuentas_generadas(cuentas_historicas)

    resultado = "\n\n".join(resultados)
    await waiting_msg.edit_text(resultado)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Falta BOT_TOKEN.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ruta", ruta))
    app.add_handler(CommandHandler("td", td))
    app.add_handler(CommandHandler("id", add_id))
    app.add_handler(CommandHandler("exid", remove_id))

    logger.info("Bot iniciado.")
    app.run_polling()


if __name__ == "__main__":
    main()
