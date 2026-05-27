from __future__ import annotations

import logging
import os
import random
import re
import asyncio
import json
from typing import Any, Callable

import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# CONFIGURACIÓN GENERAL
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8794814572:AAGTaCtmt19aqb5Dap7T6LGoRB732Rb21yM")

BANKVALIDOR_URL = os.getenv("BANKVALIDOR_URL", "https://bankvalidor.com/api/v1/validate/us")
BANKVALIDOR_ORIGIN = os.getenv("BANKVALIDOR_ORIGIN", "https://bankvalidor.com")
BANKVALIDOR_REFERER = os.getenv("BANKVALIDOR_REFERER", "https://bankvalidor.com/es")
ACCOUNT_PLACEHOLDER = os.getenv("BANKVALIDOR_ACCOUNT_PLACEHOLDER", "0000000000")

# Administradores / Vendedores autorizados
ADMIN_IDS = {6279771747, 7404196758}

# Datos del negocio
PRECIO_POR_CUENTA = 2.75
MINIMO_RECARGA = 20.0
SOPORTE_RECARGAS = r"@d333x\_cash o @isamoney01"

ROUTING_RE = re.compile(r"^\d{9}$")
PREFIJOS_OBSERVADOS = [237, 428, 437, 441]
RUTA_EJEMPLO = "011103093"

# Paquetes /full: (cantidad_cuentas, precio_usd)
PAQUETES_FULL = {
    "1": (3, 30.0),
    "2": (6, 60.0),
    "3": (10, 100.0),
}

# --- PERSISTENCIA DE DATOS (JSON) ---
ARCHIVO_CUENTAS  = "cuentas_generadas.json"
ARCHIVO_SALDOS   = "saldos_usuarios.json"
ARCHIVO_FULL     = "cuentas_full.json"

def cargar_json(archivo: str, defecto: Callable) -> Any:
    if os.path.exists(archivo):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
                return defecto(datos)
        except Exception:
            logger.error(f"Error al leer {archivo}. Se creará uno nuevo.")
    return defecto()

def guardar_json(archivo: str, datos: Any) -> None:
    try:
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=4, ensure_ascii=False)
    except Exception:
        logger.error(f"Error al guardar en {archivo}.")

# Cargar datos iniciales
cuentas_historicas = cargar_json(ARCHIVO_CUENTAS, set)
saldos_usuarios    = cargar_json(ARCHIVO_SALDOS, dict)
cuentas_full: list = cargar_json(ARCHIVO_FULL, list)

# Estado de conversación para /add
ESPERANDO_CUENTA = 1


# --- LÓGICA BANCARIA ---
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

def is_admin(update: Update) -> bool:
    user = update.effective_user
    return user and user.id in ADMIN_IDS


# --- COMANDOS DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    saldo_actual = saldos_usuarios.get(user_id, 0.0)
    disponibles = len(cuentas_full)

    msg = (
        f"👋 ¡Bienvenido!\n\n"
        f"💰 *Tu Saldo:* ${saldo_actual:.2f} USD\n"
        f"💵 *Costo por /td:* ${PRECIO_POR_CUENTA:.2f} USD\n\n"
        f"📥 *¿Cómo recargar con BTC o USDT?*\n"
        f"El monto mínimo de recarga es de *${MINIMO_RECARGA:.0f} USD*.\n"
        f"Para recargar, envía un mensaje privado a cualquiera de nuestros vendedores:\n"
        f"👤 {SOPORTE_RECARGAS}\n\n"
        f"📌 *Tu Telegram ID para recargar:* `{user_id}`\n\n"
        f"🔧 *Comandos:*\n"
        f"- `/me` — Ver tu información y saldo\n"
        f"- `/ruta <routing>` — Verificar ruta (Gratis)\n"
        f"- `/td <cantidad>` — Generar rutas y cuentas TD Bank\n\n"
        f"📦 *Paquetes Full (Ruta + Cuenta + SSN + Saldo verificado):*\n"
        f"  `/full 1` — 3 cuentas por $30 USD\n"
        f"  `/full 2` — 6 cuentas por $60 USD\n"
        f"  `/full 3` — 10 cuentas por $100 USD\n"
        f"🗂 Inventario disponible: *{disponibles} cuenta(s)*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    saldo_actual = saldos_usuarios.get(user_id, 0.0)
    await update.message.reply_text(
        f"👤 *Usuario:* {update.effective_user.first_name}\n"
        f"🆔 *ID:* `{user_id}`\n"
        f"💰 *Saldo:* ${saldo_actual:.2f} USD",
        parse_mode="Markdown"
    )


async def ruta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /ruta <numero_9_digitos>")
        return

    routing_number = context.args[0].strip()
    if not ROUTING_RE.match(routing_number):
        await update.message.reply_text("Debe tener 9 dígitos numéricos.")
        return

    checksum_ok = aba_checksum_ok(routing_number)

    try:
        data = await asyncio.to_thread(verify_routing_remote, routing_number)
        status = data.get("status", "N/A")

        if status == "valid" or (checksum_ok and status != "invalid"):
            mensaje = f"✅ {routing_number} - Válida"
        else:
            mensaje = f"❌ {routing_number} - No válida"

        await update.message.reply_text(mensaje)

    except Exception:
        logger.exception("Error en /ruta")
        if checksum_ok:
            await update.message.reply_text(f"✅ {routing_number} - Válida (verificación local)")
        else:
            await update.message.reply_text(f"❌ {routing_number} - No válida")


async def td(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    saldo_actual = saldos_usuarios.get(user_id, 0.0)

    if not context.args:
        await update.message.reply_text("Uso: /td <cantidad>")
        return

    try:
        qty = int(context.args[0])
    except ValueError:
        await update.message.reply_text("La cantidad debe ser un número entero.")
        return

    if qty < 1 or qty > 100:
        await update.message.reply_text("Cantidad permitida: entre 1 y 100.")
        return

    costo_total = qty * PRECIO_POR_CUENTA

    if saldo_actual < costo_total:
        await update.message.reply_text(
            f"❌ *Saldo Insuficiente.*\n\n"
            f"Requieres: *${costo_total:.2f} USD* para generar {qty} cuentas.\n"
            f"Tu saldo actual: *${saldo_actual:.2f} USD*.\n\n"
            f"Recarga mediante BTC/USDT (Mínimo ${MINIMO_RECARGA:.0f} USD) contactando a:\n"
            f"👤 {SOPORTE_RECARGAS}\n"
            f"Proporciónales tu ID: `{user_id}`",
            parse_mode="Markdown"
        )
        return

    waiting_msg = await update.message.reply_text("Procesando y descontando saldo...")
    await asyncio.sleep(random.randint(3, 6))

    resultados = []
    hubo_nuevas_cuentas = False

    for _ in range(qty):
        intentos = 0
        cuenta = generar_cuenta_completa()
        cuenta_str = str(cuenta)

        while cuenta_str in cuentas_historicas and intentos < 100:
            cuenta = generar_cuenta_completa()
            cuenta_str = str(cuenta)
            intentos += 1

        cuentas_historicas.add(cuenta_str)
        hubo_nuevas_cuentas = True
        resultados.append(f"TD BANK\nRUTA: {RUTA_EJEMPLO}\nCUENTA: {cuenta}")

    if hubo_nuevas_cuentas:
        guardar_json(ARCHIVO_CUENTAS, list(cuentas_historicas))

    saldos_usuarios[user_id] = saldo_actual - costo_total
    guardar_json(ARCHIVO_SALDOS, saldos_usuarios)

    resultado_final = "\n\n".join(resultados)
    resultado_final += f"\n\n💰 *Costo:* ${costo_total:.2f} USD | *Nuevo Saldo:* ${saldos_usuarios[user_id]:.2f} USD"

    await waiting_msg.edit_text(resultado_final, parse_mode="Markdown")


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("❌ No estás autorizado para usar este comando.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Uso correcto: `/saldo <cantidad> <id_usuario>`\nEjemplo: `/saldo 20 6279771747`",
            parse_mode="Markdown"
        )
        return

    try:
        cantidad = float(context.args[0])
        usuario_destino = str(context.args[1]).strip()
    except ValueError:
        await update.message.reply_text("❌ Error: La cantidad debe ser un número válido.")
        return

    saldo_previo = saldos_usuarios.get(usuario_destino, 0.0)
    nuevo_saldo = max(0.0, saldo_previo + cantidad)

    saldos_usuarios[usuario_destino] = nuevo_saldo
    guardar_json(ARCHIVO_SALDOS, saldos_usuarios)

    await update.message.reply_text(
        f"✅ *¡Saldo Actualizado exitosamente!*\n\n"
        f"🆔 *Usuario ID:* `{usuario_destino}`\n"
        f"💵 *Monto añadido:* ${cantidad:.2f} USD\n"
        f"💰 *Saldo Total actual:* ${nuevo_saldo:.2f} USD",
        parse_mode="Markdown"
    )

    try:
        await context.bot.send_message(
            chat_id=int(usuario_destino),
            text=(
                f"🎉 *¡Tu recarga ha sido procesada!*\n\n"
                f"Se han añadido *${cantidad:.2f} USD* a tu cuenta vía BTC/USDT.\n"
                f"💰 *Tu nuevo saldo total es:* ${nuevo_saldo:.2f} USD\n\n"
                f"¡Ya puedes usar el comando `/td` o comprar un paquete con `/full`!"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        logger.warning(f"No se pudo notificar al usuario {usuario_destino}.")


# --- /add  (ConversationHandler para admins) ---

async def add_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        await update.message.reply_text("❌ No estás autorizado para usar este comando.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📋 *Envía la ruta y cuenta en el siguiente formato:*\n\n"
        "```\n"
        "TD BANK\n"
        "NOMBRE: JOSE MANUEL\n"
        "SSN: 509-78-8282\n"
        "RUTA: 011103093\n"
        "CUENTA: 4357945973\n"
        "NÚMERO: +1829 282 182\n"
        "SALDO: 282.88 DÓLARES\n"
        "```\n\n"
        "Para cancelar escribe /cancel",
        parse_mode="Markdown"
    )
    return ESPERANDO_CUENTA


async def add_recibir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    texto = update.message.text.strip()

    if not texto:
        await update.message.reply_text("⚠️ Mensaje vacío. Envía los datos o escribe /cancel para cancelar.")
        return ESPERANDO_CUENTA

    cuentas_full.append(texto)
    guardar_json(ARCHIVO_FULL, cuentas_full)

    await update.message.reply_text(
        f"✅ *Cuenta agregada correctamente.*\n"
        f"📦 Total en inventario: *{len(cuentas_full)} cuenta(s)*",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def add_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END


# --- /full (compra de paquetes) ---

async def full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    saldo_actual = saldos_usuarios.get(user_id, 0.0)

    if not context.args or context.args[0] not in PAQUETES_FULL:
        await update.message.reply_text(
            "⚠️ *Elige un paquete válido:*\n\n"
            "  `/full 1` — 3 cuentas por $30 USD\n"
            "  `/full 2` — 6 cuentas por $60 USD\n"
            "  `/full 3` — 10 cuentas por $100 USD",
            parse_mode="Markdown"
        )
        return

    opcion = context.args[0]
    cantidad, precio = PAQUETES_FULL[opcion]

    if len(cuentas_full) < cantidad:
        await update.message.reply_text(
            f"❌ No hay suficientes cuentas disponibles en este momento.\n"
            f"Disponibles: *{len(cuentas_full)}* | Necesitas: *{cantidad}*\n\n"
            f"Contacta a un vendedor: {SOPORTE_RECARGAS}",
            parse_mode="Markdown"
        )
        return

    if saldo_actual < precio:
        await update.message.reply_text(
            f"❌ *Saldo Insuficiente.*\n\n"
            f"El paquete cuesta *${precio:.2f} USD*.\n"
            f"Tu saldo actual: *${saldo_actual:.2f} USD*.\n\n"
            f"Recarga contactando a: {SOPORTE_RECARGAS}\n"
            f"Tu ID: `{user_id}`",
            parse_mode="Markdown"
        )
        return

    waiting_msg = await update.message.reply_text("⏳ Procesando tu compra...")
    await asyncio.sleep(2)

    # Extraer cuentas del inventario
    cuentas_vendidas = cuentas_full[:cantidad]
    del cuentas_full[:cantidad]
    guardar_json(ARCHIVO_FULL, cuentas_full)

    # Descontar saldo
    saldos_usuarios[user_id] = saldo_actual - precio
    guardar_json(ARCHIVO_SALDOS, saldos_usuarios)

    nuevo_saldo = saldos_usuarios[user_id]

    await waiting_msg.edit_text(
        f"✅ *¡Compra exitosa! Paquete {opcion} — {cantidad} cuentas*\n"
        f"💰 Costo: ${precio:.2f} USD | Saldo restante: ${nuevo_saldo:.2f} USD\n"
        f"{'─' * 30}",
        parse_mode="Markdown"
    )

    # Enviar cada cuenta en mensaje separado
    for i, cuenta_texto in enumerate(cuentas_vendidas, 1):
        await update.message.reply_text(
            f"📄 *Cuenta {i}/{cantidad}*\n\n`{cuenta_texto}`",
            parse_mode="Markdown"
        )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # ConversationHandler para /add
    add_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_inicio)],
        states={
            ESPERANDO_CUENTA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_recibir),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancelar)],
    )

    app.add_handler(add_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("ruta", ruta))
    app.add_handler(CommandHandler("td", td))
    app.add_handler(CommandHandler("saldo", saldo))
    app.add_handler(CommandHandler("full", full))

    logger.info("Bot de Créditos Iniciado.")
    app.run_polling()


if __name__ == "__main__":
    main()