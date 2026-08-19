"""
PSO2 Animation Viewer (SFW) hosted on pso2clasic.remnoirel.com.

The translator (/ and /edit) is unchanged. This module only adds:
  GET  /Pso2AnimViewer
  GET  /pso2animviewer
  GET  /Pso2AnimViewer/download
  GET  /pso2animviewer/download

The wrench / "Return to Mod Catalog" buttons go to https://remnoirel.com/
(the 18+ catalog). Download clicks are logged to the same Discord channel
as the catalog bot (#server-status).
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
CATALOG_API_URL = (os.getenv("CATALOG_API_URL") or CATALOG_URL).rstrip("/")

# Same Discord channels the catalog bot already uses.
CANAL_ESTADO_ID = int(os.getenv("CANAL_ESTADO_ID", "1502034400119099512"))
CANAL_DESCARGAS_PSO2_ID = int(os.getenv("CANAL_DESCARGAS_PSO2_ID", "1538855225920589824"))
PSO2_POST_THREAD_ID = int(os.getenv("PSO2_POST_THREAD_ID", "1536856506295914626"))

PSO2_DOWNLOAD_URL_FALLBACK = os.getenv(
    "PSO2_DOWNLOAD_URL_FALLBACK",
    "https://mega.nz/file/BsZgUZzY#ZapLzAtAgY9osAQTm7p55NR-_zkkCfADmbyxvIbxqYM",
)
PSO2_DISCORD_POST_URL = os.getenv(
    "PSO2_DISCORD_POST_URL",
    "https://discord.com/channels/1328102593532268696/1536856506295914626/1536875746847490138",
)

DOWNLOAD_LINK_RE = re.compile(
    r"https?://(?:mega\.nz/\S+|files\.catbox\.moe/\S+|www\.mediafire\.com/\S+|mediafire\.com/\S+|drive\.google\.com/\S+)",
    re.IGNORECASE,
)

HTML_PATH = Path(__file__).with_name("pso2_anim_viewer.html")
DISCORD_ICON_SVG = (
    '<svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18">'
    '<path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C1.536 7.729.932 10.999 1.22 14.227a.076.076 0 0 0 .028.053 20.113 20.113 0 0 0 6.096 3.084.075.075 0 0 0 .081-.027 14.16 14.16 0 0 0 1.226-1.99.076.076 0 0 0-.041-.105 13.09 13.09 0 0 1-1.93-0.92.078.078 0 0 1-.008-.127c.131-.098.261-.199.387-.302a.075.075 0 0 1 .077-.01c4.057 1.86 8.441 1.86 12.446 0a.075.075 0 0 1 .078.01c.126.103.256.204.387.302a.078.078 0 0 1-.006.127 12.616 12.616 0 0 1-1.931.92.076.076 0 0 0-.04.105 14.936 14.936 0 0 0 1.226 1.99.075.075 0 0 0 .08.027 20.083 20.083 0 0 0 6.096-3.084.077.077 0 0 0 .028-.053c.358-3.791-.568-7.031-2.427-9.83a.07.07 0 0 0-.033-.027ZM8.735 12.186a2.031 2.031 0 0 1-1.921-2.158A2.031 2.031 0 0 1 8.735 7.87a2.031 2.031 0 0 1 1.921 2.158A2.031 2.031 0 0 1 8.735 12.186Zm6.529 0a2.031 2.031 0 0 1-1.921-2.158A2.031 2.031 0 0 1 15.264 7.87A2.031 2.031 0 0 1 17.185 10.028A2.031 2.031 0 0 1 15.264 12.186Z"></path>'
    "</svg>"
)

DOWNLOAD_COUNT_LOCK = asyncio.Lock()
STATE = {
    "descargas_pso2animviewer": 0,
    "mensaje_descargas_id": None,
}

_bot = None


def setup(app: web.Application, bot) -> None:
    """Register routes on the existing translator web app. Does not replace / or /edit."""
    global _bot
    _bot = bot
    app.router.add_get("/Pso2AnimViewer", page_handler)
    app.router.add_get("/pso2animviewer", page_handler)
    app.router.add_get("/Pso2AnimViewer/download", download_handler)
    app.router.add_get("/pso2animviewer/download", download_handler)
    logger.info(
        "PSO2 Animation Viewer montado en /Pso2AnimViewer "
        f"(sin modal 18+; llave → {CATALOG_URL}/)"
    )


async def cargar_contador_al_arrancar(bot) -> None:
    global _bot
    _bot = bot
    await bot.wait_until_ready()
    await cargar_contador_descargas_discord()


# ---------------------------------------------------------------------------
# Discord helpers (same message format as the catalog bot)
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


def _parsear_numero_registro(contenido):
    if not contenido:
        return None
    match = re.search(r"Descarga Pso2AnimViewer\*\*\s*`#(\d+)`", contenido)
    return int(match.group(1)) if match else None


async def _escanear_registros_descarga(canal, limit=None):
    master_msg = None
    master_count = 0
    registros = 0
    max_registro = 0
    if not canal:
        return master_msg, master_count, registros, max_registro
    bot = _bot
    async for msg in canal.history(limit=limit):
        # Count records from this bot AND from the catalog bot (same channel).
        if bot and msg.author and msg.author.bot is False:
            continue
        if "Contador de Descargas" in msg.content:
            if master_msg is None:
                master_msg = msg
                parsed = _parsear_total_descargas(msg.content)
                if parsed is not None:
                    master_count = parsed
        elif "Descarga Pso2AnimViewer" in msg.content:
            registros += 1
            num = _parsear_numero_registro(msg.content)
            if num is not None and num > max_registro:
                max_registro = num
    return master_msg, master_count, registros, max_registro


async def cargar_contador_descargas_discord():
    bot = _bot
    if bot is None or not bot.is_ready():
        return
    try:
        canal = await _obtener_canal_con_historial(CANAL_ESTADO_ID)
        master_msg, master_count, registros, max_registro = await _escanear_registros_descarga(canal)

        canal_extra = await _obtener_canal_con_historial(CANAL_DESCARGAS_PSO2_ID)
        _, extra_count, extra_reg, extra_max = await _escanear_registros_descarga(canal_extra, limit=200)

        best = max(
            STATE.get("descargas_pso2animviewer", 0),
            master_count,
            registros,
            max_registro,
            extra_count,
            extra_reg,
            extra_max,
        )
        STATE["descargas_pso2animviewer"] = best
        if master_msg:
            STATE["mensaje_descargas_id"] = master_msg.id
        logger.info(f"📥 Descargas Pso2AnimViewer restauradas: {best} (registros={registros})")
        if canal and (not master_msg or master_count < best):
            await actualizar_mensaje_descargas_discord()
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Error cargando contador: {e}")


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

        total = STATE.get("descargas_pso2animviewer", 0)
        ahora_ts = int(datetime.now(timezone.utc).timestamp())
        texto = (
            f"📥 **Contador de Descargas - PSO2 Animation Viewer**\n\n"
            f"Total de descargas: **{total}** descargas (`Pso2AnimViewer.zip`)\n"
            f"Última descarga registrada: <t:{ahora_ts}:R> (<t:{ahora_ts}:f>)\n"
            f"Cada click del botón de la web deja un registro debajo. Este mensaje no se borra al actualizar el catálogo."
        )

        if target_msg:
            await target_msg.edit(content=texto)
        else:
            nuevo_msg = await canal.send(texto)
            STATE["mensaje_descargas_id"] = nuevo_msg.id
        return True
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Discord Download Counter Error: {e}")
        return False


async def _publicar_registro_descarga(total):
    canal = await _obtener_canal_con_historial(CANAL_ESTADO_ID)
    if not canal:
        return False
    ahora_ts = int(datetime.now(timezone.utc).timestamp())
    await canal.send(
        f"📥 **Descarga Pso2AnimViewer** `#{total}`\n"
        f"Archivo: `Pso2AnimViewer.zip`\n"
        f"<t:{ahora_ts}:f> (<t:{ahora_ts}:R>)"
    )
    await actualizar_mensaje_descargas_discord()
    logger.info(f"📥 Registro de descarga #{total} publicado en #server-status")
    return True


async def _incrementar_descargas_discord():
    async with DOWNLOAD_COUNT_LOCK:
        STATE["descargas_pso2animviewer"] = STATE.get("descargas_pso2animviewer", 0) + 1
        total = STATE["descargas_pso2animviewer"]
    ok = await _publicar_registro_descarga(total)
    return ok, total


# ---------------------------------------------------------------------------
# Catalog bot fallback (keeps a single Discord writer when this bot
# is not in the Rem Noirel guild)
# ---------------------------------------------------------------------------
async def _notificar_catalogo() -> bool:
    url = f"{CATALOG_API_URL}/api/track_download"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url) as resp:
                if resp.status == 200:
                    logger.info("📥 Descarga notificada al catálogo remnoirel.com")
                    return True
                logger.warning(f"[Pso2AnimViewer] Catálogo track_download HTTP {resp.status}")
                return False
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] No se pudo notificar al catálogo: {e}")
        return False


async def _media_desde_catalogo():
    url = f"{CATALOG_API_URL}/api/pso2animviewer-media"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None, None, None
                data = await resp.json()
                return (
                    data.get("video_url"),
                    data.get("download_url"),
                    data.get("discord_post_url") or PSO2_DISCORD_POST_URL,
                )
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Media API catálogo: {e}")
        return None, None, None


async def registrar_descarga():
    """
    1 click = 1 registro. Prefer the catalog bot (already in the Rem Noirel
    guild and owner of the historical counter). If that fails, post locally.
    """
    if await _notificar_catalogo():
        return
    ok, total = await _incrementar_descargas_discord()
    if not ok:
        logger.warning(
            f"[Pso2AnimViewer] Descarga #{total} no pudo escribirse en Discord. "
            "Invita este bot al servidor Rem Noirel o deja remnoirel.com en línea."
        )


# ---------------------------------------------------------------------------
# Media (demo video + zip URL)
# ---------------------------------------------------------------------------
async def resolver_media_pso2animviewer():
    video_url = None
    download_url = None
    discord_post_url = PSO2_DISCORD_POST_URL

    cat_video, cat_dl, cat_post = await _media_desde_catalogo()
    if cat_video:
        video_url = cat_video
    if cat_post:
        discord_post_url = cat_post
    download_url = PSO2_DOWNLOAD_URL_FALLBACK
    if video_url:
        return video_url, download_url, discord_post_url

    async def scan_channel(channel_id, want_video=True, want_download=True, limit=40):
        nonlocal video_url, download_url
        channel = await _obtener_canal_con_historial(channel_id)
        if not channel:
            return
        try:
            async for msg in channel.history(limit=limit, oldest_first=False):
                for att in msg.attachments:
                    name = att.filename.lower()
                    is_video = (att.content_type and "video" in att.content_type) or name.endswith(
                        (".mp4", ".webm", ".mov")
                    )
                    is_zip = name.endswith((".zip", ".7z", ".rar")) or "pso2animviewer" in name
                    if want_video and is_video and not video_url:
                        video_url = att.url
                    if want_download and is_zip and not download_url:
                        download_url = att.url
                if want_download and not download_url and msg.content:
                    match = DOWNLOAD_LINK_RE.search(msg.content)
                    if match:
                        download_url = match.group(0).rstrip(')>"\'')
                if (not want_video or video_url) and (not want_download or download_url):
                    break
        except Exception as e:
            logger.warning(f"[Pso2AnimViewer] media scan {channel_id}: {e}")

    await scan_channel(CANAL_DESCARGAS_PSO2_ID, want_video=True, want_download=False)
    if not video_url:
        await scan_channel(PSO2_POST_THREAD_ID, want_video=True, want_download=False)
    download_url = PSO2_DOWNLOAD_URL_FALLBACK
    return video_url, download_url, discord_post_url


def _video_block(video_url, discord_post_url):
    link = (
        f'<a href="{discord_post_url}" target="_blank" rel="noopener noreferrer" class="btn-discord-link">'
        f"{DISCORD_ICON_SVG} Abrir Publicación en Discord</a>"
    )
    if video_url:
        return f"""
    <div class="video-wrapper">
        <video controls autoplay loop muted playsinline preload="metadata" referrerpolicy="no-referrer" class="guide-video">
            <source src="{video_url}" type="video/mp4">
            Tu navegador no soporta el tag de video HTML5.
        </video>
        <div style="margin-top: 15px; text-align: center;">{link}</div>
    </div>
    """
    return f"""
    <div class="video-wrapper">
        <p style="color:#b9bbbe;margin-bottom:12px;">Video preview is being refreshed from Discord. Open the original post if it does not appear yet.</p>
        <div style="margin-top: 15px; text-align: center;">{link}</div>
    </div>
    """


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
async def page_handler(request):
    video_url, _download_url, discord_post_url = await resolver_media_pso2animviewer()
    try:
        html = HTML_PATH.read_text(encoding="utf-8")
    except Exception as e:
        return web.Response(text=f"Error al cargar Pso2AnimViewer: {e}", status=500)

    html = (
        html.replace("{{VIDEO_BLOCK}}", _video_block(video_url, discord_post_url))
        .replace("{{DOWNLOAD_HREF}}", "/Pso2AnimViewer/download")
        .replace("{{DISCORD_POST_URL}}", discord_post_url)
        .replace("{{CATALOG_URL}}", f"{CATALOG_URL}/")
    )
    return web.Response(text=html, content_type="text/html")


async def download_handler(request):
    """1 click = 1 registro en el mismo canal de Discord, luego redirige al zip."""
    try:
        await registrar_descarga()
    except Exception as e:
        logger.warning(f"[Pso2AnimViewer] Download increment error: {e}")
    _video, download_url, _post = await resolver_media_pso2animviewer()
    raise web.HTTPFound(download_url or PSO2_DOWNLOAD_URL_FALLBACK)
