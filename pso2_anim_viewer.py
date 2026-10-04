"""
PSO2 Modding Tools Suite (SFW) hosted on pso2clasic.remnoirel.com.

Tools featured:
  1. PSO2 Animation Viewer (Pso2AnimViewer) - Portable 3D Viewer & Animation Swapper
  2. CMX Helper / Outfit Tool - Enables jackets & ornaments unsupported by costumes
  3. PSO2 Voice Modifier - Custom audio & voice swapper (MP3, OGG, WAV) for JP/Global

Routes:
  GET /Pso2AnimViewer
  GET /pso2animviewer
  GET /tools
  GET /programas
  GET /Pso2AnimViewer/download (redirects to anim viewer)
  GET /Pso2AnimViewer/download/anim
  GET /Pso2AnimViewer/download/cmx
  GET /Pso2AnimViewer/download/voice
  (plus lowercase aliases)

Environment Variables for Dynamic Downloads & Media (Configurable in Railway):
  - DOWNLOAD_ANIM_VIEWER (or PSO2_DOWNLOAD_URL_FALLBACK)
  - DOWNLOAD_CMX_TOOL
  - DOWNLOAD_VOICE_TOOL
  - VIDEO_ANIM_VIEWER
  - VIDEO_VOICE_MODIFIER
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

logger = logging.getLogger("discord.bot")

CATALOG_URL = (os.getenv("CATALOG_URL") or "https://remnoirel.com").rstrip("/")

# Discord channels
CANAL_ESTADO_ID = int(os.getenv("CANAL_ESTADO_ID", "1502034400119099512"))
CANAL_DESCARGAS_PSO2_ID = int(os.getenv("CANAL_DESCARGAS_PSO2_ID", "1538855225920589824"))
PSO2_POST_THREAD_ID = int(os.getenv("PSO2_POST_THREAD_ID", "1536856506295914626"))

# Default Mega links (fallbacks)
DEFAULT_DOWNLOAD_ANIM = "https://mega.nz/file/00Jn3K5J#BLJ5kym1YrjA0qHfZaZGT1oAcCSDF7k07oITONJz4Vo"
DEFAULT_DOWNLOAD_CMX = "https://mega.nz/file/ohQwWCaC#WNd-TTOkT2jnJ2FrAF2EeixfVga_-xSNLiGz0W1onLM"
DEFAULT_DOWNLOAD_VOICE = "https://mega.nz/file/J4YRmRbY#Ksk5NKEi524ibjwDqRObKwbldPpzYZP-EEOxXtZl0jo"

# Permanent Catbox media links
DEFAULT_VIDEO_ANIM = "https://files.catbox.moe/2qckjd.mp4"
DEFAULT_VIDEO_VOICE = "https://files.catbox.moe/gyvvz1.mp4"
IMG_CMX_1 = "https://files.catbox.moe/88eto9.png"
IMG_CMX_2 = "https://files.catbox.moe/qizc35.png"
IMG_VOICE_1 = "https://files.catbox.moe/wiptaj.png"
IMG_VOICE_2 = "https://files.catbox.moe/7jxgik.png"

PSO2_DISCORD_POST_URL = os.getenv(
    "PSO2_DISCORD_POST_URL",
    "https://discord.com/channels/1328102593532268696/1536856506295914626/1536875746847490138",
)

HTML_PATH = Path(__file__).with_name("pso2_anim_viewer.html")

DOWNLOAD_COUNT_LOCK = asyncio.Lock()
STATE = {
    "descargas_anim": 0,
    "descargas_cmx": 0,
    "descargas_voice": 0,
    "mensaje_descargas_id": None,
}

_bot = None


def get_download_url_anim() -> str:
    return (
        os.getenv("DOWNLOAD_ANIM_VIEWER")
        or os.getenv("PSO2_DOWNLOAD_URL_FALLBACK")
        or DEFAULT_DOWNLOAD_ANIM
    )


def get_download_url_cmx() -> str:
    return os.getenv("DOWNLOAD_CMX_TOOL") or DEFAULT_DOWNLOAD_CMX


def get_download_url_voice() -> str:
    return os.getenv("DOWNLOAD_VOICE_TOOL") or DEFAULT_DOWNLOAD_VOICE


def get_video_url_anim() -> str:
    return os.getenv("VIDEO_ANIM_VIEWER") or DEFAULT_VIDEO_ANIM


def get_video_url_voice() -> str:
    return os.getenv("VIDEO_VOICE_MODIFIER") or DEFAULT_VIDEO_VOICE


def setup(app: web.Application, bot) -> None:
    """Registra rutas en la aplicación web existente."""
    global _bot
    _bot = bot

    # Páginas principales
    for r in ("/Pso2AnimViewer", "/pso2animviewer", "/tools", "/programas"):
        app.router.add_get(r, page_handler)

    # Descargas individuales
    for r in ("/Pso2AnimViewer/download", "/pso2animviewer/download"):
        app.router.add_get(r, download_handler_anim)

    for r in ("/Pso2AnimViewer/download/anim", "/pso2animviewer/download/anim"):
        app.router.add_get(r, download_handler_anim)

    for r in ("/Pso2AnimViewer/download/cmx", "/pso2animviewer/download/cmx"):
        app.router.add_get(r, download_handler_cmx)

    for r in ("/Pso2AnimViewer/download/voice", "/pso2animviewer/download/voice"):
        app.router.add_get(r, download_handler_voice)

    logger.info("PSO2 Modding Tools Suite montado en /Pso2AnimViewer")


async def cargar_contador_al_arrancar(bot) -> None:
    global _bot
    _bot = bot
    await bot.wait_until_ready()
    await cargar_contador_descargas_discord()


# ---------------------------------------------------------------------------
# Discord helpers con contadores separados para cada programa
# ---------------------------------------------------------------------------
async def _obtener_canal_con_historial(channel_id):
    bot = _bot
    if bot is None or not bot.is_ready():
        return None
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            channel = await bot.fetch_channel(channel_id)
        if channel is None:
            return None
        import discord

        if isinstance(channel, discord.ForumChannel):
            return None
        return channel
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Canal {channel_id} no accesible: {e}")
        return None


def _parsear_total_descargas(contenido):
    if not contenido:
        return None
    match = re.search(r"Total de descargas:\s*\*\*(\d+)\*\*", contenido)
    return int(match.group(1)) if match else None


def _parsear_numero_registro(contenido, app_label):
    if not contenido:
        return None
    match = re.search(rf"Descarga {re.escape(app_label)}\*\*\s*`#(\d+)`", contenido)
    return int(match.group(1)) if match else None


async def _escanear_registros_descarga(canal, limit=None):
    master_msg = None
    master_anim = 0
    max_anim = 0
    max_cmx = 0
    max_voice = 0
    if not canal:
        return master_msg, max_anim, max_cmx, max_voice
    bot = _bot
    async for msg in canal.history(limit=limit):
        if bot and msg.author and msg.author.bot is False:
            continue
        if "Contador de Descargas" in msg.content or "Contadores de Descargas" in msg.content:
            if master_msg is None:
                master_msg = msg
                parsed = _parsear_total_descargas(msg.content)
                if parsed is not None:
                    master_anim = parsed

        n_anim = _parsear_numero_registro(msg.content, "Pso2AnimViewer")
        if n_anim and n_anim > max_anim:
            max_anim = n_anim

        n_cmx = _parsear_numero_registro(msg.content, "CMX Helper")
        if n_cmx and n_cmx > max_cmx:
            max_cmx = n_cmx

        n_voice = _parsear_numero_registro(msg.content, "PSO2 Voice Modifier")
        if n_voice and n_voice > max_voice:
            max_voice = n_voice

    return master_msg, max(master_anim, max_anim), max_cmx, max_voice


async def cargar_contador_descargas_discord():
    bot = _bot
    if bot is None or not bot.is_ready():
        return
    try:
        canal = await _obtener_canal_con_historial(CANAL_ESTADO_ID)
        master_msg, anim_cnt, cmx_cnt, voice_cnt = await _escanear_registros_descarga(canal)

        canal_extra = await _obtener_canal_con_historial(CANAL_DESCARGAS_PSO2_ID)
        _, extra_anim, extra_cmx, extra_voice = await _escanear_registros_descarga(canal_extra, limit=200)

        STATE["descargas_anim"] = max(STATE.get("descargas_anim", 0), anim_cnt, extra_anim)
        STATE["descargas_cmx"] = max(STATE.get("descargas_cmx", 0), cmx_cnt, extra_cmx)
        STATE["descargas_voice"] = max(STATE.get("descargas_voice", 0), voice_cnt, extra_voice)

        if master_msg:
            STATE["mensaje_descargas_id"] = master_msg.id
        logger.info(
            f"📥 Contadores restaurados: Anim={STATE['descargas_anim']}, "
            f"CMX={STATE['descargas_cmx']}, Voice={STATE['descargas_voice']}"
        )
        if canal and not master_msg:
            await actualizar_mensaje_descargas_discord()
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Error cargando contadores: {e}")


async def actualizar_mensaje_descargas_discord():
    bot = _bot
    if bot is None or not bot.is_ready():
        return False
    try:
        canal = await _obtener_canal_con_historial(CANAL_ESTADO_ID)
        if not canal:
            return False

        msg_id = STATE.get("mensaje_descargas_id")
        target_msg = None
        if msg_id:
            try:
                target_msg = await canal.fetch_message(msg_id)
            except Exception:
                target_msg = None

        if not target_msg:
            target_msg, _, _, _ = await _escanear_registros_descarga(canal)
            if target_msg:
                STATE["mensaje_descargas_id"] = target_msg.id

        total_anim = STATE.get("descargas_anim", 0)
        total_cmx = STATE.get("descargas_cmx", 0)
        total_voice = STATE.get("descargas_voice", 0)
        ahora_ts = int(datetime.now(timezone.utc).timestamp())
        texto = (
            f"📥 **Contadores de Descargas - Modding Tools Suite**\n\n"
            f"🎬 **PSO2 Animation Viewer:** **{total_anim}** descargas (`Pso2AnimViewer.zip`)\n"
            f"👗 **CMX Helper Tool:** **{total_cmx}** descargas (`CMX_Helper_Tool.zip`)\n"
            f"🎙️ **PSO2 Voice Modifier:** **{total_voice}** descargas (`PSO2_Voice_Modifier.zip`)\n\n"
            f"Última descarga registrada: <t:{ahora_ts}:R> (<t:{ahora_ts}:f>)\n"
            f"Cada click en los botones de la web deja su propio registro debajo."
        )

        if target_msg:
            await target_msg.edit(content=texto)
        else:
            nuevo_msg = await canal.send(texto)
            STATE["mensaje_descargas_id"] = nuevo_msg.id
        return True
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Discord Master Counter Error: {e}")
        return False


async def _publicar_registro_descarga(total, app_name, filename):
    canal = await _obtener_canal_con_historial(CANAL_ESTADO_ID)
    if not canal:
        return False
    ahora_ts = int(datetime.now(timezone.utc).timestamp())
    await canal.send(
        f"📥 **Descarga {app_name}** `#{total}`\n"
        f"Archivo: `{filename}`\n"
        f"<t:{ahora_ts}:f> (<t:{ahora_ts}:R>)"
    )
    await actualizar_mensaje_descargas_discord()
    logger.info(f"📥 Registro de descarga #{total} ({app_name}) publicado en #server-status")
    return True


async def _incrementar_descargas_discord(tool_key, app_label, filename):
    async with DOWNLOAD_COUNT_LOCK:
        STATE[tool_key] = STATE.get(tool_key, 0) + 1
        total = STATE[tool_key]
    ok = await _publicar_registro_descarga(total, app_name=app_label, filename=filename)
    return ok, total


async def registrar_descarga(tool_key, app_label, filename):
    """Registra 1 clic de descarga de forma separada para cada programa."""
    ok, total = await _incrementar_descargas_discord(tool_key, app_label, filename)
    if not ok:
        logger.warning(f"[Pso2AnimViewer] Descarga #{total} ({app_label}) no pudo escribirse en Discord.")


# ---------------------------------------------------------------------------
# Handlers HTTP
# ---------------------------------------------------------------------------
async def page_handler(request):
    try:
        html = HTML_PATH.read_text(encoding="utf-8")
    except Exception as e:
        return web.Response(text=f"Error al cargar Suite de Programas: {e}", status=500)

    html = (
        html.replace("{{VIDEO_ANIM_SRC}}", get_video_url_anim())
        .replace("{{VIDEO_VOICE_SRC}}", get_video_url_voice())
        .replace("{{IMG_CMX_1}}", IMG_CMX_1)
        .replace("{{IMG_CMX_2}}", IMG_CMX_2)
        .replace("{{IMG_VOICE_1}}", IMG_VOICE_1)
        .replace("{{IMG_VOICE_2}}", IMG_VOICE_2)
        .replace("{{DOWNLOAD_ANIM_HREF}}", "/Pso2AnimViewer/download/anim")
        .replace("{{DOWNLOAD_CMX_HREF}}", "/Pso2AnimViewer/download/cmx")
        .replace("{{DOWNLOAD_VOICE_HREF}}", "/Pso2AnimViewer/download/voice")
        .replace("{{DISCORD_POST_URL}}", PSO2_DISCORD_POST_URL)
        .replace("{{CATALOG_URL}}", f"{CATALOG_URL}/")
    )
    return web.Response(text=html, content_type="text/html")


async def download_handler_anim(request):
    try:
        await registrar_descarga("descargas_anim", "Pso2AnimViewer", "Pso2AnimViewer.zip")
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Download increment error: {e}")
    target = get_download_url_anim()
    raise web.HTTPFound(target)


async def download_handler_cmx(request):
    try:
        await registrar_descarga("descargas_cmx", "CMX Helper", "CMX_Helper_Tool.zip")
    except Exception as e:
        logger.warning(f"[CMX_Helper] Download error: {e}")
    target = get_download_url_cmx()
    raise web.HTTPFound(target)


async def download_handler_voice(request):
    try:
        await registrar_descarga("descargas_voice", "PSO2 Voice Modifier", "PSO2_Voice_Modifier.zip")
    except Exception as e:
        logger.warning(f"[PSO2_Voice_Modifier] Download error: {e}")
    target = get_download_url_voice()
    raise web.HTTPFound(target)
