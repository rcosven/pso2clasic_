import discord
from discord import app_commands
from discord.ext import commands
import os
import csv
import logging
from pathlib import Path
import unicodedata
import base64
import requests
import time
import json
import asyncio
from aiohttp import web
import pso2_anim_viewer

# Configurar variables de GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "rcosven/pso2clasic_")
GITHUB_BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "main")

# URL pública del Traductor Visual (sin barra final).
# En Railway: PUBLIC_URL=https://pso2clasic.remnoirel.com
# (subdominio propio, sin modal de edad del catálogo en remnoirel.com)
PUBLIC_URL_DEFAULT = "https://pso2clasic.remnoirel.com"


def get_public_url() -> str:
    """
    URL base del traductor para botones de Discord y logs.
    Prioridad: PUBLIC_URL → RAILWAY_PUBLIC_DOMAIN → default del subdominio → localhost.
    """
    explicit = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    railway_domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip().rstrip("/")
    if railway_domain:
        if railway_domain.startswith("http://") or railway_domain.startswith("https://"):
            return railway_domain
        return f"https://{railway_domain}"
    # En producción se espera el subdominio del traductor (sin modal NSFW).
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
        return PUBLIC_URL_DEFAULT.rstrip("/")
    return "http://localhost:5000"


# Configurar el logger
logger = logging.getLogger("discord.bot")

class BuscadorBot(commands.Bot):
    def __init__(self):
        # 1. Configurar los Intents
        intents = discord.Intents.default()
        intents.message_content = True 
        
        super().__init__(command_prefix="!", intents=intents)
        
        # 2. Lista en memoria para búsquedas rápidas
        self.index_datos = []
        # True cuando cargar_indices terminó (Railway healthcheck no debe esperar esto)
        self.index_ready = False
        self.index_loading = False
        self.index_error = None

        # 2b. Líneas/archivos nuevos del update (para el botón «Líneas nuevas» de la web)
        #     key: (corpus, file_basename, section, group, id)
        self.new_line_keys = set()
        #     archivos completos nuevos: (corpus, file_basename) cuando el CSV es 100% nuevo
        self.new_file_stems = set()

        # 3. Conjunto para rastrear archivos modificados localmente
        self.modified_files = set()

    async def setup_hook(self):
        # 0. Web PRIMERO (puerto abierto al instante → Railway no mata el contenedor)
        try:
            await start_web_server(self)
        except Exception as e:
            logger.error(f"Error al iniciar el servidor web: {e}")

        try:
            asyncio.create_task(pso2_anim_viewer.cargar_contador_al_arrancar(self))
        except Exception as e:
            logger.error(f"Error al cargar contador Pso2AnimViewer: {e}")

        # 1. Índices en background (puede tardar 20–60s con ~1.2M filas)
        self.loop.create_task(self._load_indices_bg())

        # 2. Sync Discord en background (rate-limit 429 no debe tumbar el deploy)
        self.loop.create_task(self._sync_discord_commands_bg())

    async def _load_indices_bg(self):
        """Carga CSV sin bloquear el puerto HTTP ni el login de Discord."""
        if self.index_loading:
            return
        self.index_loading = True
        self.index_ready = False
        self.index_error = None
        try:
            logger.info("Cargando índices CSV en segundo plano...")
            await asyncio.to_thread(self.cargar_indices)
            self.index_ready = True
            logger.info(
                f"Índices listos para la web. IDs: {len(self.index_datos)}"
            )
        except Exception as e:
            self.index_error = str(e)
            logger.error(f"Error cargando índices en background: {e}")
        finally:
            self.index_loading = False

    async def _sync_discord_commands_bg(self):
        """Sincroniza slash commands sin bloquear setup_hook / healthcheck."""
        await asyncio.sleep(2)  # deja respirar al login
        mi_servidor = discord.Object(id=1525057654446100553)
        try:
            self.tree.copy_global_to(guild=mi_servidor)
            logger.info("Sincronizando comandos de barra en el servidor de pruebas...")
            await self.tree.sync(guild=mi_servidor)
            logger.info("Sync de guild OK.")
        except Exception as e:
            logger.warning(
                f"Sync guild omitido/falló (no crítico para la web): {e}"
            )

        try:
            # Global sync es lento y a menudo pega 429; no es necesario para la web
            if os.getenv("DISCORD_SYNC_GLOBAL", "").strip().lower() in (
                "1", "true", "yes", "on"
            ):
                logger.info("Sincronizando comandos globalmente...")
                await self.tree.sync()
                logger.info("Sync global OK.")
            else:
                logger.info(
                    "Sync global Discord desactivado "
                    "(pon DISCORD_SYNC_GLOBAL=1 para forzarlo)."
                )
        except Exception as e:
            logger.warning(f"Sync global omitido/falló: {e}")

    @staticmethod
    def _norm_search(s: str) -> str:
        """Normaliza texto para búsqueda (minúsculas, sin tildes)."""
        if not s:
            return ""
        return "".join(
            c
            for c in unicodedata.normalize("NFKD", s.lower())
            if not unicodedata.combining(c)
        )

    @staticmethod
    def fix_utf16_swapped(s: str) -> str:
        """
        Repara texto corrupto por UTF-16LE leído como UTF-16BE
        (p.ej. 'You' → '夀漀甀', '<br>' → '㰀戀爀㸀').
        Cada codepoint con byte bajo 0x00 se interpreta como el byte alto (ASCII/Latin-1).
        """
        if not s:
            return s
        out: list[str] = []
        for ch in s:
            cp = ord(ch)
            if cp > 0xFF and (cp & 0xFF) == 0:
                hi = cp >> 8
                # ASCII / control + Latin-1 útil
                if hi <= 0xFF:
                    out.append(chr(hi))
                    continue
            # Espacios “raros” frecuentemente producidos por la misma corrupción
            if cp in (0x2000, 0x2001, 0x2002, 0x2003, 0x3000):
                out.append(" ")
                continue
            out.append(ch)
        return "".join(out)

    @staticmethod
    def is_utf16_mojibake_char(ch: str) -> bool:
        """
        Un carácter «raro» del patrón UTF-16 LE/BE intercambiado (U+XX00).

        Ejemplos (XX = byte ASCII original):
          't' → 琀 (U+7400)   'T' → 吀 (U+5400)
          'o' → 漀 (U+6F00)   'l' → 氀 (U+6C00)
          'Y' → 夀 (U+5900)   'u' → 甀 (U+7500)
          '<' → 㰀  'b' → 戀  'r' → 爀  '>' → 㸀   (= <br>)

        Excluye U+3000 (espacio ideográfico), habitual en textos OK del juego.
        """
        if not ch:
            return False
        cp = ord(ch)
        if cp == 0x3000:
            return False
        # U+XX00 con XX = ASCII imprimible (espacio .. ~)
        if cp > 0xFF and (cp & 0xFF) == 0:
            hi = cp >> 8
            return 0x20 <= hi <= 0x7E
        return False

    @staticmethod
    def count_utf16_mojibake_chars(s: str) -> int:
        """Cuenta caracteres basura U+XX00 en el texto."""
        if not s:
            return 0
        return sum(1 for ch in s if BuscadorBot.is_utf16_mojibake_char(ch))

    @staticmethod
    def is_utf16_swapped_corrupt(s: str) -> bool:
        """
        True si el texto CONTIENE al menos un carácter basura (琀, 吀, 漀, 氀, …).

        No exige la frase entera corrupta: con un solo carácter raro basta.
        La búsqueda «Líneas raras» además filtra solo group 1.
        """
        return BuscadorBot.count_utf16_mojibake_chars(s) >= 1

    @staticmethod
    def has_rare_chars(s: str) -> bool:
        """Contiene algún carácter basura UTF-16 (琀 吀 漀 氀 夀 㰀 …)."""
        return BuscadorBot.is_utf16_swapped_corrupt(s)

    def cargar_lineas_nuevas(self):
        """
        Carga listas de líneas/archivos nuevos del update para la web.

        Archivos esperados (UTF-8, header: file,section,group,id,text):
          data/lineas_nuevas/LINEAS_NUEVAS_group1_Classic.csv
          data/lineas_nuevas/LINEAS_NUEVAS_group1_NGS.csv
        """
        self.new_line_keys.clear()
        self.new_file_stems.clear()
        base = Path("data") / "lineas_nuevas"
        sources = [
            ("classic", base / "LINEAS_NUEVAS_group1_Classic.csv"),
            ("ng", base / "LINEAS_NUEVAS_group1_NGS.csv"),
        ]
        loaded_rows = 0
        for corpus, path in sources:
            if not path.exists():
                logger.warning(f"Lista de líneas nuevas no encontrada: {path}")
                continue
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        fname = (row.get("file") or "").strip()
                        if not fname:
                            continue
                        if not fname.lower().endswith(".csv"):
                            fname = fname + ".csv"
                        stem = Path(fname).name
                        section = (row.get("section") or "").strip()
                        group = str(row.get("group") or "").strip()
                        row_id = (row.get("id") or "").strip()
                        self.new_line_keys.add((corpus, stem, section, group, row_id))
                        self.new_file_stems.add((corpus, stem))
                        loaded_rows += 1
            except Exception as e:
                logger.error(f"Error cargando líneas nuevas {path}: {e}")
        logger.info(
            f"Líneas nuevas cargadas: {loaded_rows} filas | "
            f"keys={len(self.new_line_keys)} | archivos={len(self.new_file_stems)}"
        )

    def is_new_line_item(self, item: dict) -> bool:
        """True si la fila del índice está en la lista de líneas nuevas del update."""
        fpath = (item.get("file") or "").replace("\\", "/")
        # Solo CSV editables del parche (no *_Raw)
        if not (fpath.startswith("Csv_Clasic/") or fpath.startswith("Csv_Ngs/")):
            return False
        if "_Raw/" in fpath:
            return False
        corpus = "classic" if fpath.startswith("Csv_Clasic/") else "ng"
        stem = Path(fpath).name
        section = (item.get("section") or "").strip()
        group = str(item.get("group") or "").strip()
        row_id = (item.get("id") or "").strip()
        return (corpus, stem, section, group, row_id) in self.new_line_keys

    def cargar_indices(self):
        """Lee los CSV locales y guarda los IDs y textos para búsquedas."""
        self.index_datos.clear()
        self.cargar_lineas_nuevas()

        # Agrega aquí los nombres de las carpetas que contienen tus CSV
        directorios_datos = ["Csv_Clasic", "Csv_Ngs", "Csv_Ngs_Raw", "Csv_Clasic_Raw"]
        corrupt_count = 0
        new_flag_count = 0

        for dir_name in directorios_datos:
            ruta = Path(dir_name)
            if ruta.exists():
                for archivo_csv in ruta.glob("*.csv"):
                    try:
                        # utf-8-sig previene errores de formato con Excel/GitHub
                        with open(archivo_csv, 'r', encoding='utf-8-sig') as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                if 'id' in row:
                                    section = row.get('section', '') or ''
                                    group = row.get('group', '') or ''
                                    row_id = row.get('id', '') or ''
                                    texto_original = row.get('text', '') or ''
                                    texto_norm = self._norm_search(texto_original)
                                    # Comando CSV: section,group,id[,text]
                                    # Permite encontrar filas vacías o por clave (ej. Basic,1,Explanation)
                                    cmd = f"{section},{group},{row_id}"
                                    cmd_full = f"{cmd},{texto_original}"
                                    # Basura UTF-16: solo tiene sentido marcarlo en group 1
                                    # (traducción ES/EN). Group 0 es JP y daba falsos positivos.
                                    is_g1 = str(group) == "1"
                                    is_corrupt = (
                                        self.is_utf16_swapped_corrupt(texto_original)
                                        if is_g1
                                        else False
                                    )
                                    is_rare = is_corrupt  # misma definición (solo group 1)
                                    text_fixed = (
                                        self.fix_utf16_swapped(texto_original) if is_corrupt else ""
                                    )
                                    if is_rare:
                                        corrupt_count += 1
                                    file_rel = f"{dir_name}/{archivo_csv.name}"
                                    corpus = (
                                        "classic" if dir_name.startswith("Csv_Clasic")
                                        else "ng" if dir_name.startswith("Csv_Ngs")
                                        else "other"
                                    )
                                    is_new = False
                                    if corpus in ("classic", "ng") and not dir_name.endswith("_Raw"):
                                        is_new = (
                                            corpus,
                                            archivo_csv.name,
                                            section.strip(),
                                            str(group).strip(),
                                            (row_id or "").strip(),
                                        ) in self.new_line_keys
                                        if is_new:
                                            new_flag_count += 1
                                    self.index_datos.append({
                                        'section': section,
                                        'group': group,
                                        'id': row_id,
                                        'text': texto_original,
                                        'text_norm': texto_norm,
                                        'cmd': cmd,
                                        'cmd_norm': self._norm_search(cmd),
                                        'cmd_full_norm': self._norm_search(cmd_full),
                                        'id_norm': self._norm_search(row_id),
                                        'section_norm': self._norm_search(section),
                                        'file': file_rel,
                                        'line': reader.line_num,
                                        'rare_chars': is_rare,
                                        'corrupt_utf16': is_corrupt,
                                        'text_fixed': text_fixed,
                                        'text_fixed_norm': self._norm_search(text_fixed) if text_fixed else '',
                                        'is_new_line': is_new,
                                    })
                    except Exception as e:
                        logger.error(f"Error leyendo {archivo_csv.name}: {e}")
            else:
                logger.warning(f"Advertencia: No se encontró la carpeta {dir_name}")

        logger.info(
            f"Índices cargados correctamente. Total de IDs: {len(self.index_datos)} "
            f"(líneas raras group1 UTF-16: {corrupt_count}; "
            f"líneas nuevas del update en índice: {new_flag_count})"
        )

def modificar_texto_csv(
    file_path: str,
    orig_section: str,
    orig_group: str,
    orig_id: str,
    nuevo_texto: str,
    nueva_section: str = None,
    nuevo_group: str = None,
    nuevo_id: str = None,
    create_if_missing: bool = False,
):
    """
    Lee un archivo CSV, busca la fila exacta por section, group e id original,
    y reemplaza el texto, o incluso la section/group/id si se especifican.
    Si create_if_missing=True y no existe la fila, la añade al final.
    """
    ruta = Path(file_path)
    if not ruta.exists():
        return False

    filas = []
    headers = []
    modificado = False

    with open(ruta, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or ["section", "group", "id", "text"])
        for row in reader:
            # Si hay columnas extra por comas sin escapar en el CSV original, unirlas al texto
            if None in row:
                extra_data = row.pop(None)
                if isinstance(extra_data, list):
                    row['text'] = row.get('text', '') + "," + ",".join(extra_data)

            if row.get('section', '') == orig_section and row.get('group') == orig_group and row.get('id') == orig_id:
                row['text'] = nuevo_texto
                if nueva_section is not None:
                    row['section'] = nueva_section
                if nuevo_group is not None:
                    row['group'] = nuevo_group
                if nuevo_id is not None:
                    row['id'] = nuevo_id
                modificado = True
            filas.append(row)

    if not modificado and create_if_missing:
        # Crear fila nueva (p. ej. group 1 de traducción que aún no existe)
        new_row = {h: "" for h in headers}
        new_row["section"] = nueva_section if nueva_section is not None else (orig_section or "")
        new_row["group"] = nuevo_group if nuevo_group is not None else (orig_group or "1")
        new_row["id"] = nuevo_id if nuevo_id is not None else (orig_id or "")
        new_row["text"] = nuevo_texto
        filas.append(new_row)
        modificado = True

    if not modificado:
        return False

    with open(ruta, mode='w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers, lineterminator='\r\n')
        writer.writeheader()
        writer.writerows(filas)
        
    return True

def obtener_contexto_csv(file_path: str):
    """
    Lee el archivo CSV y agrupa las líneas por (section, id).
    Retorna un texto formateado amigablemente con los pares (Original vs Traducción).
    """
    ruta = Path(file_path)
    if not ruta.exists():
        return "No se encontró el archivo."

    # Agrupar por (section, id) -> {'0': original, '1': traduccion}
    grupos = {}
    with open(ruta, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sec = row.get('section', '')
            row_id = row.get('id', '')
            grp = row.get('group', '')
            text = row.get('text', '')
            
            key = (sec, row_id)
            if key not in grupos:
                grupos[key] = {}
            grupos[key][grp] = text

    # Formatear el texto
    lineas_resultado = [f"📖 **Líneas de traducción en `{ruta.name}`:**\n"]
    items = list(grupos.items())
    max_items = 12
    for (sec, row_id), grp_dict in items[:max_items]:
        original = grp_dict.get('0', '*(Sin texto original)*')
        traduccion = grp_dict.get('1', '*(Sin traducción)*')
        
        bloque = (
            f"🔑 **ID:** `{row_id}` (Sección: `{sec}`)\n"
            f"🇯🇵 **Original:** {original}\n"
            f"🇪🇸 **Traducción:** {traduccion}\n"
            f"----------------------------------"
        )
        lineas_resultado.append(bloque)
        
    if len(items) > max_items:
        lineas_resultado.append(f"\n*... y {len(items) - max_items} líneas más en el archivo.*")
        
    resultado_completo = "\n".join(lineas_resultado)
    if len(resultado_completo) > 1950:
        resultado_completo = resultado_completo[:1940] + "\n\n*(Mensaje truncado por límite de longitud en Discord...)*"
        
    return resultado_completo

def crear_pull_request_traduccion(ruta_archivo_local: str, ruta_archivo_repo: str, row_id: str, usuario_discord: str):
    """
    Crea una rama temporal a partir de la rama base, sube el archivo modificado y genera un Pull Request.
    """
    if not GITHUB_TOKEN:
        return None, "GITHUB_TOKEN no está configurado en las variables de entorno."

    base_url = f"https://api.github.com/repos/{GITHUB_REPO}"
    nombre_branch_seguro = "".join(c if c.isalnum() or c in "-_" else "_" for c in row_id)[:30]
    nombre_rama = f"translation-{nombre_branch_seguro}-{int(time.time())}"
    headers_api = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    
    # 1. Obtener SHA de la rama base
    res = requests.get(f"{base_url}/git/ref/heads/{GITHUB_BASE_BRANCH}", headers=headers_api)
    if res.status_code != 200:
        return None, f"Error al obtener rama base '{GITHUB_BASE_BRANCH}': {res.text}"
    base_sha = res.json()["object"]["sha"]

    # 2. Crear rama temporal
    payload_ref = {
        "ref": f"refs/heads/{nombre_rama}",
        "sha": base_sha
    }
    res = requests.post(f"{base_url}/git/refs", headers=headers_api, json=payload_ref)
    if res.status_code != 201:
        return None, f"Error al crear rama temporal: {res.text}"

    # 3. Obtener SHA del archivo original en el repo (para poder actualizarlo)
    res = requests.get(f"{base_url}/contents/{ruta_archivo_repo}?ref={GITHUB_BASE_BRANCH}", headers=headers_api)
    file_sha = None
    if res.status_code == 200:
        file_sha = res.json()["sha"]

    # 4. Codificar archivo en Base64
    try:
        with open(ruta_archivo_local, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return None, f"Error al leer archivo local: {e}"

    # 5. Subir el cambio a la rama temporal
    payload_content = {
        "message": f"Traduccion sugerida por {usuario_discord} para ID: {row_id}",
        "content": content_b64,
        "branch": nombre_rama
    }
    if file_sha:
        payload_content["sha"] = file_sha

    # Normalizar ruta del archivo para URL
    ruta_archivo_repo_url = ruta_archivo_repo.replace("\\", "/")
    res = requests.put(f"{base_url}/contents/{ruta_archivo_repo_url}", headers=headers_api, json=payload_content)
    if res.status_code not in [200, 201]:
        return None, f"Error al actualizar archivo en GitHub: {res.text}"

    # 6. Crear el Pull Request (cuenta del bot/token; el traductor no necesita GitHub)
    payload_pr = {
        "title": f"📝 Sugerencia: {row_id} por {usuario_discord}",
        "head": nombre_rama,
        "base": GITHUB_BASE_BRANCH,
        "body": (
            f"## Sugerencia de traducción (sin cuenta GitHub del autor)\n\n"
            f"- **Autor (nick):** `{usuario_discord}`\n"
            f"- **ID / lote:** `{row_id}`\n"
            f"- **Archivo:** `{ruta_archivo_repo}`\n\n"
            f"Esta PR la abrió el bot del proyecto de forma anónima para el editor. "
            f"**Solo contribuidores del repositorio deben hacer merge** tras revisar los cambios.\n"
        )
    }
    res = requests.post(f"{base_url}/pulls", headers=headers_api, json=payload_pr)
    if res.status_code == 201:
        return res.json()["html_url"], None
    else:
        return None, f"Error al crear Pull Request: {res.text}"


def _github_headers():
    if not GITHUB_TOKEN:
        return None
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def listar_pull_requests_abiertos(base: str | None = None):
    """
    Lista PRs abiertos del repositorio (paginado).
    Devuelve (lista_de_prs, error_str).
    """
    headers = _github_headers()
    if not headers:
        return None, "GITHUB_TOKEN no está configurado en las variables de entorno."

    base_url = f"https://api.github.com/repos/{GITHUB_REPO}"
    branch_base = base or GITHUB_BASE_BRANCH
    prs = []
    page = 1

    while True:
        res = requests.get(
            f"{base_url}/pulls",
            headers=headers,
            params={
                "state": "open",
                "base": branch_base,
                "sort": "created",
                "direction": "asc",
                "per_page": 100,
                "page": page,
            },
            timeout=20,
        )
        if res.status_code != 200:
            return None, f"Error al listar PRs: {res.status_code} {res.text[:300]}"

        batch = res.json()
        if not batch:
            break
        prs.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return prs, None


def merge_pull_request(pr_number: int, merge_method: str = "squash", commit_title: str | None = None):
    """
    Hace merge de un PR por número (sin pre-chequeo largo de mergeable).
    merge_method: 'merge' | 'squash' | 'rebase'
    Devuelve (ok: bool, mensaje: str).
    """
    headers = _github_headers()
    if not headers:
        return False, "GITHUB_TOKEN no está configurado."

    base_url = f"https://api.github.com/repos/{GITHUB_REPO}"
    payload = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title

    # Hasta 3 intentos: rate-limit / "Base branch was modified" / red
    last_err = "Error desconocido"
    for attempt in range(1, 4):
        try:
            res = requests.put(
                f"{base_url}/pulls/{pr_number}/merge",
                headers=headers,
                json=payload,
                timeout=20,
            )
        except requests.Timeout:
            last_err = f"Timeout (20s) en intento {attempt}"
            time.sleep(1.5 * attempt)
            continue
        except requests.RequestException as e:
            last_err = f"Red: {type(e).__name__}: {e}"
            time.sleep(1.5 * attempt)
            continue

        if res.status_code == 200:
            sha = (res.json().get("sha") or "")[:7]
            return True, f"Merged (sha `{sha}`)" if sha else "Merged"

        # Ya estaba mergeado
        if res.status_code == 405:
            try:
                msg = res.json().get("message", res.text)
            except Exception:
                msg = res.text
            msg_l = (msg or "").lower()
            if "already been merged" in msg_l or "pull request is not open" in msg_l:
                return True, "Ya estaba mergeado"
            return False, f"No mergeable: {str(msg)[:180]}"

        if res.status_code == 409:
            # Base cambió: reintentar tras breve espera
            last_err = f"Conflicto/base actualizada (intento {attempt})"
            time.sleep(1.5 * attempt)
            continue

        if res.status_code in (403, 429):
            # Rate limit secundario de GitHub
            retry_after = res.headers.get("Retry-After") or res.headers.get("retry-after")
            try:
                wait_s = min(int(retry_after), 30) if retry_after else 5 * attempt
            except ValueError:
                wait_s = 5 * attempt
            last_err = f"Rate limit {res.status_code}, esperando {wait_s}s"
            time.sleep(wait_s)
            continue

        try:
            detail = res.json().get("message", res.text)
        except Exception:
            detail = res.text
        return False, f"Error {res.status_code}: {str(detail)[:200]}"

    return False, last_err


def puede_usar_merge(interaction: discord.Interaction) -> bool:
    """Solo administradores del servidor Discord (o dueño del servidor)."""
    if not interaction.guild:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


def construir_mensaje_archivo(bot_instance, filepath: str, match_item: dict = None):
    # Encontrar el número de líneas traducibles para información general
    count = 0
    for item in bot_instance.index_datos:
        if item['file'] == filepath and item.get('group') == '1':
            count += 1

    mensaje_lineas = [
        f"📁 **Archivo:** `{filepath}`",
        f"📊 Contiene {count} líneas traducibles.",
        f"",
        f"💡 Haz clic en **Abrir Editor Visual** abajo para traducir directamente en el navegador."
    ]
    
    if match_item:
        mensaje_lineas.append(f"*(Se abrirá directamente en la línea:* `{match_item['id']}`*)*")
        
    return "\n".join(mensaje_lineas)

class DescargarCSVView(discord.ui.View):
    def __init__(self, bot_instance, filepath: str, target_id: str = None):
        super().__init__(timeout=180)
        self.bot = bot_instance
        self.filepath = filepath

        public_url = get_public_url()
        url_edit = f"{public_url}/edit?file={filepath}"
        if target_id:
            url_edit += f"&id={target_id}"

        # Botón para el Editor Web (apunta al subdominio del traductor, sin modal de edad)
        self.add_item(discord.ui.Button(
            label="Abrir Editor Visual (Recomendado)", 
            style=discord.ButtonStyle.link, 
            url=url_edit, 
            emoji="🌐", 
            row=0
        ))

    @discord.ui.button(label="Descargar CSV", style=discord.ButtonStyle.primary, emoji="📥", row=1)
    async def descargar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            archivo = discord.File(self.filepath)
            await interaction.response.send_message(
                content=f"Aquí tienes el archivo `{self.filepath}`:",
                file=archivo,
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error al enviar el archivo: {e}", ephemeral=True)

    @discord.ui.button(label="Subir a GitHub", style=discord.ButtonStyle.success, emoji="📤", row=1)
    async def github_pr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        pr_url, error = crear_pull_request_traduccion(
            ruta_archivo_local=self.filepath,
            ruta_archivo_repo=self.filepath,
            row_id="MultipleUpdates",
            usuario_discord=str(interaction.user)
        )
        
        if error:
            await interaction.followup.send(f"❌ Error: {error}", ephemeral=True)
        else:
            if self.filepath in self.bot.modified_files:
                self.bot.modified_files.remove(self.filepath)
            await interaction.followup.send(f"✅ **Pull Request creado exitosamente:**\n🔗 <{pr_url}>", ephemeral=True)

class DescargarDropdown(discord.ui.Select):
    def __init__(self, bot_instance, files: list):
        self.bot = bot_instance
        options = []
        for fpath in files[:25]:
            label = fpath[-100:]
            options.append(discord.SelectOption(label=label, value=fpath, emoji="📁"))
            
        super().__init__(
            placeholder="Elige un archivo para traducir...",
            min_values=1,
            max_values=1,
            options=options
        )
        
    async def callback(self, interaction: discord.Interaction):
        filepath = self.values[0]
        mensaje = construir_mensaje_archivo(self.bot, filepath)
        view = DescargarCSVView(self.bot, filepath)
        await interaction.response.send_message(
            content=mensaje,
            view=view,
            ephemeral=True
        )

class DescargarMultipleView(discord.ui.View):
    def __init__(self, bot_instance, files: list):
        super().__init__(timeout=180)
        self.add_item(DescargarDropdown(bot_instance, files))

# --- WEB SERVER (TRADUCTOR VISUAL) ---
async def web_home(request):
    try:
        with open("web_search.html", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
    except Exception as e:
        return web.Response(text=f"Error al cargar Buscador: {e}", status=500)

async def web_index(request):
    try:
        with open("web_ui.html", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
    except Exception as e:
        return web.Response(text=f"Error al cargar UI: {e}", status=500)

def _item_corpus(file_path: str) -> str:
    """
    Clasifica un path del índice: 'classic' | 'ng' | 'other'.
    Acepta Csv_Clasic, Csv_Clasic_Raw, Csv_Ngs, Csv_Ngs_Raw (con o sin _Raw).
    """
    f = (file_path or "").replace("\\", "/").lower()
    # Quitar carpeta editable normalizada
    base = f.split("/", 1)[0].replace("_raw", "")
    if "clasic" in base or "classic" in base:
        return "classic"
    if "ngs" in base or base.endswith("/ng") or base == "ng" or "csv_ng" in base:
        return "ng"
    # fallback por subcadena en path completo
    if "clasic" in f or "classic" in f:
        return "classic"
    if "ngs" in f or "/ng/" in f:
        return "ng"
    return "other"


def _exact_line_key(s: str) -> str:
    """
    Clave de igualdad EXACTA de la línea de texto.
    Solo normaliza finales de línea; un carácter distinto → otra clave.
    """
    if not s:
        return ""
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def _seed_phrase_from_query(q: str) -> str:
    """
    Si pegan 'section,group,id,texto' toma el texto;
    si no, usa la query entera como frase.
    """
    if not q:
        return ""
    try:
        rows = list(csv.reader([q]))
        if rows and len(rows[0]) >= 4:
            return rows[0][3] or ""
        if rows and len(rows[0]) == 1:
            return rows[0][0]
    except Exception:
        pass
    return q


def _file_layer(fpath: str) -> str:
    """main | raw | other — para badges de resultados."""
    f = (fpath or "").replace("\\", "/")
    if "_Raw/" in f or f.startswith("Csv_Clasic_Raw/") or f.startswith("Csv_Ngs_Raw/"):
        return "raw"
    if f.startswith("Csv_Clasic/") or f.startswith("Csv_Ngs/"):
        return "main"
    return "other"


async def web_api_search(request):
    bot = request.app["bot"]
    # Mientras cargan ~1.2M filas, no tumbar la web: respuesta clara
    if not getattr(bot, "index_ready", False):
        return web.json_response(
            {
                "items": [],
                "total": 0,
                "total_pages": 0,
                "page": 1,
                "per_page": 50,
                "loading": True,
                "error": "Índice aún cargando. Espera unos segundos y reintenta.",
            },
            status=503,
        )

    query = request.query.get("q", "").strip()
    # deep=1 | true | yes  → también busca en section/group/id (comandos CSV)
    deep_raw = (request.query.get("deep") or "").strip().lower()
    deep = deep_raw in ("1", "true", "yes", "on")

    # new=1 / nuevas=1 → líneas y archivos del update (listas en data/lineas_nuevas/)
    # rare/corrupt se reutilizan como alias del mismo modo (botón web antiguo)
    new_raw = (
        request.query.get("new")
        or request.query.get("nuevas")
        or request.query.get("rare")
        or request.query.get("corrupt")
        or ""
    ).strip().lower()
    new_only = new_raw in ("1", "true", "yes", "on")
    rare_only = new_only  # alias retrocompatible para la UI

    # file=1 / byfile=1 / filename=1 → buscar por nombre de archivo CSV
    file_raw = (
        request.query.get("file")
        or request.query.get("byfile")
        or request.query.get("filename")
        or request.query.get("archivo")
        or ""
    ).strip().lower()
    file_only = file_raw in ("1", "true", "yes", "on")

    # equal=1 / iguales=1 / same=1 → líneas group 1 con texto EXACTAMENTE idéntico
    equal_raw = (
        request.query.get("equal")
        or request.query.get("iguales")
        or request.query.get("same")
        or request.query.get("dup")
        or ""
    ).strip().lower()
    equal_only = equal_raw in ("1", "true", "yes", "on")
    # chars / min_chars = longitud MÍNIMA del texto (filtra "??", "-", "aina", etc.)
    # NO es match parcial: la igualdad sigue siendo del texto completo.
    try:
        equal_min_chars = int(
            request.query.get("chars")
            or request.query.get("min_chars")
            or request.query.get("n")
            or "20"
        )
    except ValueError:
        equal_min_chars = 20
    equal_min_chars = max(1, min(500, equal_min_chars))

    # scope=all|classic|ng  (default: all = Classic + NGS)
    scope_raw = (request.query.get("scope") or "all").strip().lower()
    if scope_raw in ("classic", "clasic", "c", "win32"):
        scope = "classic"
    elif scope_raw in ("ng", "ngs", "n", "reboot"):
        scope = "ng"
    else:
        scope = "all"

    # Paginación: page (1-based), per_page (default 50, max 100)
    try:
        page = max(1, int(request.query.get("page") or "1"))
    except ValueError:
        page = 1
    try:
        per_page = int(request.query.get("per_page") or "50")
    except ValueError:
        per_page = 50
    per_page = max(10, min(100, per_page))

    # Tope de coincidencias a recolectar (evita respuestas enormes en queries muy genéricas)
    MAX_MATCHES = 5000

    empty = {
        "items": [],
        "deep": deep,
        "new": new_only,
        "nuevas": new_only,
        "file": file_only,
        "byfile": file_only,
        "equal": equal_only,
        "iguales": equal_only,
        "chars": equal_min_chars,
        "min_chars": equal_min_chars,
        "rare": rare_only,
        "corrupt": rare_only,
        "scope": scope,
        "page": page,
        "per_page": per_page,
        "total": 0,
        "total_pages": 0,
        "capped": False,
    }
    # En modo líneas nuevas se permite query vacía (lista todo sin escribir nada)
    # Líneas iguales: query opcional (sin query = textos idénticos repetidos 2+ veces)
    # Por archivo: mínimo 2 caracteres (ej. "cl" / "ms")
    min_q = 2 if file_only else 3
    if not new_only and not equal_only and len(query) < min_q:
        return web.json_response(empty)

    # ─── Modo líneas iguales (group 1): MAIN vs RAW del MISMO archivo/clave ─
    # Empareja Csv_Ngs/foo.csv ↔ Csv_Ngs_Raw/foo.csv (y Classic igual).
    # Solo sale si el text de group 1 es EXACTAMENTE idéntico en ambos.
    # Mín. chars filtra basura corta; no es match parcial.
    if equal_only:
        seed_raw = _seed_phrase_from_query(query)
        seed_key = _exact_line_key(seed_raw) if seed_raw else None
        if seed_key is not None and not seed_key:
            empty["error"] = "La frase de búsqueda está vacía."
            return web.json_response(empty)
        if seed_key is not None and len(seed_key) < equal_min_chars:
            empty["error"] = (
                f"La frase tiene {len(seed_key)} caracteres; "
                f"mínimo configurado = {equal_min_chars}. Baja «Mín. chars» o usa una frase más larga."
            )
            empty["chars"] = equal_min_chars
            empty["min_chars"] = equal_min_chars
            return web.json_response(empty)

        # key = (corpus, stem, section, id) → { "main": item, "raw": item }
        pairs: dict[tuple, dict] = {}
        for item in bot.index_datos:
            if str(item.get("group", "") or "").strip() != "1":
                continue
            corpus = _item_corpus(item.get("file", ""))
            if scope == "classic" and corpus != "classic":
                continue
            if scope == "ng" and corpus != "ng":
                continue
            if corpus not in ("classic", "ng"):
                continue

            fpath = (item.get("file") or "").replace("\\", "/")
            layer = _file_layer(fpath)
            if layer not in ("main", "raw"):
                continue

            stem = Path(fpath).name  # common.csv
            section = (item.get("section") or "").strip()
            row_id = (item.get("id") or "").strip()
            text = item.get("text") or ""
            tkey = _exact_line_key(text)
            if not tkey or len(tkey) < equal_min_chars:
                continue

            if seed_key is not None and tkey != seed_key:
                continue

            pk = (corpus, stem, section, row_id)
            slot = pairs.setdefault(pk, {})
            # Si hay duplicados en el mismo layer, nos quedamos con el primero
            if layer not in slot:
                slot[layer] = item

        coincidencias = []
        # Solo pares donde MAIN y RAW existen y el texto es idéntico
        ranked_keys = []
        for pk, slot in pairs.items():
            main_it = slot.get("main")
            raw_it = slot.get("raw")
            if not main_it or not raw_it:
                continue
            main_t = _exact_line_key(main_it.get("text") or "")
            raw_t = _exact_line_key(raw_it.get("text") or "")
            if not main_t or main_t != raw_t:
                continue  # un carácter distinto → no es "igual"
            ranked_keys.append((pk[0], pk[1], pk[2], pk[3], main_t, main_it, raw_it))

        # Orden: archivo, section, id
        ranked_keys.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

        for corpus, stem, section, row_id, tkey, main_it, raw_it in ranked_keys:
            # Solo se muestra/abre MAIN. RAW es solo referencia de comparación.
            item = main_it
            fpath = (item.get("file") or "").replace("\\", "/")
            open_file = fpath.replace("_Raw", "")  # nunca abrir raw
            coincidencias.append({
                "file": open_file,
                "id": item.get("id", ""),
                "section": item.get("section", ""),
                "group": item.get("group", ""),
                "text": item.get("text", ""),
                "cmd": item.get("cmd", ""),
                "match": "equal",
                "corpus": corpus,
                "layer": "main",
                "exact": True,
                "main_raw": True,
                "min_chars": equal_min_chars,
                "text_len": len(tkey),
                "pair_stem": stem,
            })
            if len(coincidencias) >= MAX_MATCHES:
                break

        total = len(coincidencias)
        total_pages = (total + per_page - 1) // per_page if total else 0
        if total_pages and page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        page_items = coincidencias[start : start + per_page]
        return web.json_response({
            "items": page_items,
            "deep": False,
            "new": False,
            "nuevas": False,
            "file": False,
            "byfile": False,
            "equal": True,
            "iguales": True,
            "exact": True,
            "main_raw": True,
            "chars": equal_min_chars,
            "min_chars": equal_min_chars,
            "pair_count": total,
            "rare": False,
            "corrupt": False,
            "scope": scope,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "capped": total >= MAX_MATCHES,
        })

    query_norm = "".join(
        c
        for c in unicodedata.normalize("NFKD", query.lower())
        if not unicodedata.combining(c)
    ) if query else ""
    # Quitar espacios alrededor de comas: "Basic, 1, Explanation" → "basic,1,explanation"
    query_cmd = ",".join(p.strip() for p in query_norm.split(",")) if query_norm else ""
    # Permitir "Basic,1,Explanation," (coma final de text vacío)
    query_cmd = query_cmd.rstrip(",")
    # Nombre de archivo: aceptar con o sin .csv
    query_file = query_norm
    if query_file.endswith(".csv"):
        query_file_stem = query_file[:-4]
    else:
        query_file_stem = query_file
    query_file_csv = query_file_stem + ".csv"

    coincidencias = []
    ids_vistos = set()

    for item in bot.index_datos:
        # Filtro Classic / NGS / ambos
        corpus = _item_corpus(item.get("file", ""))
        if scope == "classic" and corpus != "classic":
            continue
        if scope == "ng" and corpus != "ng":
            continue

        matched = False
        match_where = "text"

        if new_only:
            # ═══════════════════════════════════════════════════════════
            # Líneas nuevas: filas listadas en data/lineas_nuevas/*
            # (archivos y keys del update de Classic/NGS). Solo editables.
            # ═══════════════════════════════════════════════════════════
            fpath = (item.get("file") or "").replace("\\", "/")
            if "_Raw/" in fpath or "/Csv_Clasic_Raw/" in fpath or "/Csv_Ngs_Raw/" in fpath:
                continue
            if not (
                fpath.startswith("Csv_Clasic/")
                or fpath.startswith("Csv_Ngs/")
            ):
                continue

            is_new = bool(item.get("is_new_line")) or bot.is_new_line_item(item)
            if not is_new:
                continue

            matched = True
            match_where = "new"
            # Filtro opcional: acotar por archivo / id / texto / comando
            if query_norm:
                if (
                    query_norm not in item.get("text_norm", "")
                    and query_norm not in item.get("id_norm", "")
                    and query_norm not in item.get("cmd_norm", "")
                    and query_norm not in item.get("section_norm", "")
                    and query_norm not in fpath.lower()
                ):
                    continue
        elif file_only:
            # ═══════════════════════════════════════════════════════════
            # Por nombre de archivo: cl0421450101 / cl0421450101.csv
            # Solo CSV editables (no *_Raw). Coincide stem, nombre o ruta.
            # ═══════════════════════════════════════════════════════════
            fpath = (item.get("file") or "").replace("\\", "/")
            if "_Raw/" in fpath or fpath.endswith("_Raw"):
                continue
            if not (
                fpath.startswith("Csv_Clasic/")
                or fpath.startswith("Csv_Ngs/")
            ):
                continue

            fname = Path(fpath).name.lower()  # ej. trial_boss_weak_evolution.csv
            stem = Path(fname).stem.lower()   # sin .csv
            fpath_l = fpath.lower()

            # Coincidencia flexible:
            # - exacta stem / nombre.csv
            # - substring en stem o nombre (para parciales)
            if (
                query_file_stem == stem
                or query_file_csv == fname
                or query_file == fname
                or query_file_stem in stem
                or query_file in fname
                or query_file in fpath_l
            ):
                matched = True
                match_where = "file"
            else:
                continue
        else:
            # Búsqueda normal: solo en el texto de la línea
            if query_norm in item.get("text_norm", ""):
                matched = True
                match_where = "text"
            elif deep:
                # Búsqueda avanzada: section, group, id y comando CSV
                cmd_norm = item.get("cmd_norm", "")
                cmd_full = item.get("cmd_full_norm", "")
                if (
                    query_cmd
                    and (
                        query_cmd in cmd_norm
                        or query_cmd in cmd_full
                        or cmd_norm in query_cmd
                    )
                ):
                    matched = True
                    match_where = "command"
                elif query_norm in item.get("id_norm", ""):
                    matched = True
                    match_where = "id"
                elif query_norm in item.get("section_norm", ""):
                    matched = True
                    match_where = "section"
                elif query_norm in (item.get("group") or "").lower():
                    matched = True
                    match_where = "group"

        if not matched:
            continue

        # Siempre MAIN editable: raw solo se usa para comparar, nunca se abre
        editable_file = (item.get("file") or "").replace("\\", "/").replace("_Raw", "")
        # Clave única: section+id+group (mismo id puede existir en varias sections)
        clave_unica = f"{editable_file}_{item.get('section','')}_{item['id']}_{item.get('group','')}"

        if clave_unica not in ids_vistos:
            ids_vistos.add(clave_unica)
            entry = {
                "file": editable_file,
                "id": item["id"],
                "section": item.get("section", ""),
                "group": item.get("group", ""),
                "text": item["text"],
                "cmd": item.get("cmd", ""),
                "match": match_where,
                "corpus": corpus if corpus != "other" else _item_corpus(editable_file),
            }
            if new_only or item.get("is_new_line"):
                entry["new"] = True
                entry["nuevas"] = True
            # Mantener flags de rareza solo si realmente hay basura UTF-16
            if item.get("rare_chars") or item.get("corrupt_utf16"):
                entry["rare"] = True
                entry["corrupt"] = True
                fixed = item.get("text_fixed") or ""
                if fixed:
                    entry["text_fixed"] = fixed
            coincidencias.append(entry)

        if len(coincidencias) >= MAX_MATCHES:
            break

    total = len(coincidencias)
    total_pages = (total + per_page - 1) // per_page if total else 0
    if total_pages and page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    page_items = coincidencias[start : start + per_page]

    return web.json_response({
        "items": page_items,
        "deep": deep,
        "new": new_only,
        "nuevas": new_only,
        "file": file_only,
        "byfile": file_only,
        "rare": rare_only,
        "corrupt": rare_only,
        "scope": scope,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "capped": total >= MAX_MATCHES,
    })

async def web_api_file(request):
    filename = request.query.get("name")
    if not filename:
        return web.json_response({"error": "Falta parámetro 'name'"}, status=400)
        
    bot = request.app['bot']
    items = []
    
    # 1. Obtener todos los items del archivo (O(N))
    file_items = [item for item in bot.index_datos if item['file'] == filename]
    
    # 2. Obtener los items del archivo Raw (O(N))
    raw_filename = filename.replace("Csv_Ngs", "Csv_Ngs_Raw").replace("Csv_Clasic", "Csv_Clasic_Raw")
    raw_items = [item for item in bot.index_datos if item['file'] == raw_filename]
    
    # 3. Agrupar por (section, id): group 0 = JP, group 1 = ES (u otros grupos)
    #    Antes solo se devolvía group==1 → archivos solo con group 0 salían vacíos en el editor.
    by_key = {}
    order = []
    for item in file_items:
        key = (item.get("section", ""), item["id"])
        if key not in by_key:
            by_key[key] = {
                "section": item.get("section", ""),
                "id": item["id"],
                "groups": {},
                "line": item.get("line", 0),
            }
            order.append(key)
        by_key[key]["groups"][str(item.get("group", ""))] = item.get("text", "")
        # Conservar la línea más baja como ancla de orden
        ln = item.get("line", 0) or 0
        if ln and (not by_key[key]["line"] or ln < by_key[key]["line"]):
            by_key[key]["line"] = ln

    g_raw_dict = {
        (item.get("section"), item["id"]): item.get("text", "")
        for item in raw_items
        if str(item.get("group", "")) == "1"
    }

    # Orden estable: por línea de aparición
    order.sort(key=lambda k: (by_key[k]["line"] or 0, k[0], k[1]))

    for key in order:
        entry = by_key[key]
        groups = entry["groups"]
        original_text = groups.get("0", "")
        spanish_text = groups.get("1", "")
        # Si no hay group 1, el texto “principal” visible puede ser g0 u otro grupo
        other_groups = {g: t for g, t in groups.items() if g not in ("0", "1")}
        preview = spanish_text or original_text
        if not preview and other_groups:
            preview = next(iter(other_groups.values()), "")

        items.append({
            "section": entry["section"],
            "id": entry["id"],
            "group": "1" if "1" in groups else ("0" if "0" in groups else next(iter(groups.keys()), "1")),
            "text": spanish_text,  # español (puede ir vacío si aún no hay traducción)
            "original": original_text,
            "english": g_raw_dict.get(key, ""),
            "has_group1": "1" in groups,
            "has_group0": "0" in groups,
            "groups": groups,
            "preview": preview,
        })
            
    return web.json_response({"items": items})

async def web_api_file_raw(request):
    filename = request.query.get("name")
    if not filename:
        return web.json_response({"error": "Falta parámetro 'name'"}, status=400)
        
    bot = request.app['bot']
    file_items = [item for item in bot.index_datos if item['file'] == filename]
    file_items.sort(key=lambda x: x.get('line', 0))
    
    items = []
    for item in file_items:
        items.append({
            'section': item.get('section', ''),
            'group': item.get('group', ''),
            'id': item['id'],
            'text': item.get('text', ''),
            'line': item.get('line', 0)
        })
            
    return web.json_response({"items": items})

async def web_api_save(request):
    try:
        data = await request.json()
        filename = data.get('file')
        orig_section = data.get('orig_section', data.get('section', ''))
        orig_group = str(data.get('orig_group', data.get('group', '1')) or '1')
        orig_id = data.get('orig_id', data.get('id'))
        
        new_section = data.get('section')
        new_group = data.get('group')
        new_id = data.get('id')
        new_text = data.get('text', '')
        # Por defecto crear la fila si no existe (p. ej. group 1 de una línea solo JP)
        create_if_missing = data.get('create_if_missing', True)
        
        bot = request.app['bot']
        
        exito = modificar_texto_csv(
            filename,
            orig_section,
            orig_group,
            orig_id,
            new_text,
            new_section,
            new_group,
            new_id,
            create_if_missing=bool(create_if_missing),
        )
        if not exito:
            # DEBUG: find out WHY it failed
            ruta = Path(filename)
            debug_info = f"Exists: {ruta.exists()}. "
            if ruta.exists():
                with open(ruta, 'r', encoding='utf-8-sig') as f:
                    all_rows = list(csv.DictReader(f))
                    debug_info += f"Total rows in file: {len(all_rows)}. "
                    matches = [r for r in all_rows if r.get('id') == orig_id]
                    debug_info += f"Matches found: {len(matches)}. "
                    for m in matches:
                        debug_info += f"[sec: '{m.get('section')}', grp: '{m.get('group')}'] "
            return web.json_response({"error": f"No se pudo modificar CSV. {debug_info}"}, status=500)
            
        # Actualizar índice en memoria (modificar o insertar)
        updated = False
        for item in bot.index_datos:
            if item['file'] == filename and item.get('section') == orig_section and item['id'] == orig_id and str(item.get('group', '')) == orig_group:
                item['text'] = new_text
                if new_section is not None: item['section'] = new_section
                if new_group is not None: item['group'] = str(new_group)
                if new_id is not None: item['id'] = new_id
                # refrescar flags de rareza (solo group 1 cuenta como «línea rara»)
                g_now = str(item.get("group", ""))
                is_corrupt = (
                    BuscadorBot.is_utf16_swapped_corrupt(new_text) if g_now == "1" else False
                )
                item['rare_chars'] = is_corrupt
                item['corrupt_utf16'] = is_corrupt
                item['text_fixed'] = (
                    BuscadorBot.fix_utf16_swapped(new_text) if is_corrupt else ""
                )
                item['text_fixed_norm'] = (
                    BuscadorBot._norm_search(item['text_fixed']) if item['text_fixed'] else ""
                )
                item['text_norm'] = BuscadorBot._norm_search(new_text)
                updated = True
                break
        if not updated:
            final_section = new_section if new_section is not None else orig_section
            final_group = str(new_group if new_group is not None else orig_group)
            final_id = new_id if new_id is not None else orig_id
            is_corrupt = (
                BuscadorBot.is_utf16_swapped_corrupt(new_text) if final_group == "1" else False
            )
            is_rare = is_corrupt
            text_fixed = BuscadorBot.fix_utf16_swapped(new_text) if is_corrupt else ""
            cmd = f"{final_section},{final_group},{final_id}"
            bot.index_datos.append({
                "section": final_section or "",
                "group": final_group,
                "id": final_id or "",
                "text": new_text,
                "text_norm": BuscadorBot._norm_search(new_text),
                "cmd": cmd,
                "cmd_norm": BuscadorBot._norm_search(cmd),
                "cmd_full_norm": BuscadorBot._norm_search(f"{cmd},{new_text}"),
                "id_norm": BuscadorBot._norm_search(final_id or ""),
                "section_norm": BuscadorBot._norm_search(final_section or ""),
                "file": filename,
                "line": 0,
                "rare_chars": is_rare,
                "corrupt_utf16": is_corrupt,
                "text_fixed": text_fixed,
                "text_fixed_norm": BuscadorBot._norm_search(text_fixed) if text_fixed else "",
            })
                
        bot.modified_files.add(filename)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def web_api_github(request):
    """
    Crea un PR anónimo en nombre del proyecto (GITHUB_TOKEN del bot).
    El editor NO necesita cuenta de GitHub. El nick es opcional (créditos);
    si no se envía, se atribuye como Anónimo.
    Solo contribuidores con Write en el repo pueden hacer merge de main.
    """
    try:
        data = await request.json()
        filename = data.get('file')
        nickname = (data.get('nickname') or '').strip()
        bot = request.app['bot']

        if not filename:
            return web.json_response({"error": "Falta el archivo a enviar."}, status=400)

        # Nick opcional: sanitizar si hay; si no, Anónimo
        if nickname:
            nickname_safe = "".join(
                c for c in nickname if c.isalnum() or c in " ._-"
            ).strip()[:40]
            if not nickname_safe:
                return web.json_response({
                    "error": "El nick solo puede usar letras, números, espacios, . _ - (o déjalo vacío para anónimo)."
                }, status=400)
            autor_pr = f": {nickname_safe}"
            author_display = nickname_safe
        else:
            nickname_safe = "Anónimo"
            autor_pr = "Anónimo"
            author_display = "Anónimo"

        pr_url, error = crear_pull_request_traduccion(
            ruta_archivo_local=filename,
            ruta_archivo_repo=filename,
            row_id="WebUpdate",
            usuario_discord=autor_pr
        )

        if error:
            return web.json_response({"error": error}, status=500)

        if filename in bot.modified_files:
            bot.modified_files.remove(filename)

        # Notificar en Discord al canal 1525352131111026709
        try:
            channel = bot.get_channel(1525352131111026709)
            if channel:
                await channel.send(
                    f"🚀 **¡Nueva sugerencia anónima (sin cuenta GitHub)!**\n"
                    f"**Autor (nick):** `{author_display}`\n"
                    f"**Archivo:** `{filename}`\n"
                    f"**Solo contribuidores pueden hacer merge:** {pr_url}"
                )
        except Exception as e:
            print(f"Error al enviar notificación de Discord: {e}")

        return web.json_response({"success": True, "url": pr_url, "author": author_display})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def web_health(request):
    """
    Healthcheck de Railway / proxy: siempre 200 si el proceso vive.
    No depende de que los CSV estén indexados.
    """
    bot = request.app.get("bot")
    ready = bool(bot and getattr(bot, "index_ready", False))
    loading = bool(bot and getattr(bot, "index_loading", False))
    n = len(bot.index_datos) if bot and getattr(bot, "index_datos", None) is not None else 0
    err = getattr(bot, "index_error", None) if bot else None
    return web.json_response(
        {
            "ok": True,
            "status": "ready" if ready else ("loading" if loading else "starting"),
            "index_ready": ready,
            "index_loading": loading,
            "index_count": n,
            "index_error": err,
        },
        status=200,
    )


async def start_web_server(bot):
    app = web.Application()
    app['bot'] = bot
    # Health primero: Railway/proxy lo pegan y no deben esperar índices
    app.router.add_get('/health', web_health)
    app.router.add_get('/healthz', web_health)
    app.router.add_get('/', web_home)
    app.router.add_get('/edit', web_index)
    app.router.add_get('/api/search', web_api_search)
    app.router.add_get('/api/file', web_api_file)
    app.router.add_get('/api/file_raw', web_api_file_raw)
    app.router.add_post('/api/save', web_api_save)
    app.router.add_post('/api/github', web_api_github)
    pso2_anim_viewer.setup(app, bot)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Railway provee la variable PORT dinámica. Si no existe, usamos 5000 para pruebas locales.
    port = int(os.getenv("PORT", 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    # Prevenir que Python elimine el servidor de la memoria guardando referencias
    bot.web_runner = runner
    bot.web_site = site
    
    public_url = get_public_url()
    logger.info(
        f"Servidor Web (Traductor Visual) iniciado en puerto {port}. "
        f"URL pública: {public_url} (sin modal de edad; el catálogo NSFW está en remnoirel.com)"
    )

bot = BuscadorBot()

# --- COMANDOS DE BARRA (SLASH COMMANDS) ---

@bot.tree.command(name="buscar_id", description="Busca un fragmento de texto en los archivos CSV")
@app_commands.describe(id_buscado="El texto que deseas encontrar")
async def buscar_id(interaction: discord.Interaction, id_buscado: str):
    query = id_buscado.lower()
    coincidencias = []
    
    for item in bot.index_datos:
        if query in item['text'].lower():
            coincidencias.append(item)
            
    if not coincidencias:
        await interaction.response.send_message(f"❌ No se encontraron coincidencias para: **{id_buscado}**")
        return
        
    # Obtener archivos únicos
    archivos_unicos = []
    for item in coincidencias:
        if item['file'] not in archivos_unicos:
            archivos_unicos.append(item['file'])
            
    total = len(coincidencias)
    if total == 1:
        match = coincidencias[0]
        mensaje = construir_mensaje_archivo(bot, match['file'], match)
        view = DescargarCSVView(bot, match['file'])
        await interaction.response.send_message(mensaje, view=view)
    else:
        limite = 5
        lineas = [f"✅ **Se encontraron {total} coincidencias (mostrando las primeras {limite}):**"]
        for match in coincidencias[:limite]:
            lineas.append(
                f"📁 `{match['file']}` (Línea {match['line']})\n"
                f"   📝 *Texto:* {match['text'][:150]}"
            )
        if total > limite:
            lineas.append(f"*... y {total - limite} coincidencias más.*")
            
        if len(archivos_unicos) == 1:
            mensaje = construir_mensaje_archivo(bot, archivos_unicos[0], coincidencias[0])
            view = DescargarCSVView(bot, archivos_unicos[0])
        else:
            view = DescargarMultipleView(bot, archivos_unicos)
            lineas.append("\n💡 *Usa la lista desplegable de abajo para elegir un archivo para traducir.*")
            if len(archivos_unicos) > 25:
                lineas.append("⚠️ *Hay más de 25 archivos. Se muestran los primeros 25 en la lista desplegable.*")
            
        if len(archivos_unicos) == 1:
            await interaction.response.send_message(mensaje, view=view)
        else:
            mensaje_completo = "\n".join(lineas)
            if len(mensaje_completo) > 2000:
                mensaje_completo = mensaje_completo[:1990] + "\n..."
            await interaction.response.send_message(mensaje_completo, view=view)

@bot.tree.command(name="recargar", description="Vuelve a leer los archivos CSV sin reiniciar el bot")
async def recargar(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    if bot.index_loading:
        await interaction.followup.send("⏳ Ya hay una recarga en curso. Espera un momento.")
        return
    bot.index_ready = False
    bot.index_loading = True
    try:
        await asyncio.to_thread(bot.cargar_indices)
        bot.index_ready = True
        await interaction.followup.send(
            f"🔄 Datos recargados con éxito. IDs mapeados: {len(bot.index_datos)}"
        )
    except Exception as e:
        bot.index_error = str(e)
        await interaction.followup.send(f"❌ Error al recargar: {e}")
    finally:
        bot.index_loading = False


@bot.tree.command(
    name="listar_prs",
    description="Lista los Pull Requests abiertos del repositorio GitHub",
)
async def listar_prs(interaction: discord.Interaction):
    if not puede_usar_merge(interaction):
        await interaction.response.send_message(
            "❌ Solo administradores del servidor pueden usar este comando.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    prs, error = await asyncio.to_thread(listar_pull_requests_abiertos)
    if error:
        await interaction.followup.send(f"❌ {error}", ephemeral=True)
        return
    if not prs:
        await interaction.followup.send(
            f"✅ No hay PRs abiertos hacia `{GITHUB_BASE_BRANCH}` en `{GITHUB_REPO}`.",
            ephemeral=True,
        )
        return

    lineas = [
        f"📋 **{len(prs)} PR(s) abiertos** en `{GITHUB_REPO}` → `{GITHUB_BASE_BRANCH}`:\n"
    ]
    for pr in prs[:25]:
        num = pr.get("number")
        title = (pr.get("title") or "")[:70]
        user = (pr.get("user") or {}).get("login", "?")
        lineas.append(f"• **#{num}** — {title} *(by {user})*")
    if len(prs) > 25:
        lineas.append(f"\n*... y {len(prs) - 25} más.*")
    lineas.append("\n💡 Usa `/merge_all` para mergear todos (solo admins).")

    msg = "\n".join(lineas)
    if len(msg) > 2000:
        msg = msg[:1990] + "\n..."
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(
    name="merge_all",
    description="Hace merge de TODOS los Pull Requests abiertos (solo administradores)",
)
@app_commands.describe(
    metodo="Método de merge en GitHub",
    dry_run="Si es True, solo lista qué haría sin mergear",
)
@app_commands.choices(
    metodo=[
        app_commands.Choice(name="squash (recomendado)", value="squash"),
        app_commands.Choice(name="merge commit", value="merge"),
        app_commands.Choice(name="rebase", value="rebase"),
    ]
)
async def merge_all(
    interaction: discord.Interaction,
    metodo: app_commands.Choice[str] = None,
    dry_run: bool = False,
):
    """
    Lista PRs abiertos y los mergea uno a uno (del más antiguo al más nuevo).
    Requiere GITHUB_TOKEN con permiso de escritura en el repo.
    """
    if not puede_usar_merge(interaction):
        await interaction.response.send_message(
            "❌ Solo administradores del servidor pueden usar este comando.",
            ephemeral=True,
        )
        return

    if not GITHUB_TOKEN:
        await interaction.response.send_message(
            "❌ `GITHUB_TOKEN` no está configurado en el entorno del bot.",
            ephemeral=True,
        )
        return

    merge_method = (metodo.value if metodo else "squash")
    await interaction.response.defer(ephemeral=False)

    try:
        prs, error = await asyncio.wait_for(
            asyncio.to_thread(listar_pull_requests_abiertos),
            timeout=45,
        )
    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Timeout al listar PRs en GitHub (45s).")
        return

    if error:
        await interaction.followup.send(f"❌ {error}")
        return
    if not prs:
        await interaction.followup.send(
            f"✅ No hay PRs abiertos hacia `{GITHUB_BASE_BRANCH}`."
        )
        return

    if dry_run:
        preview = "\n".join(
            f"• #{pr['number']} — {(pr.get('title') or '')[:60]}" for pr in prs[:30]
        )
        extra = f"\n*... y {len(prs) - 30} más.*" if len(prs) > 30 else ""
        await interaction.followup.send(
            f"🔎 **Dry-run:** se mergearían **{len(prs)}** PR(s) con método `{merge_method}`:\n{preview}{extra}"
        )
        return

    status_msg = await interaction.followup.send(
        f"⏳ Mergeando **{len(prs)}** PR(s) con método `{merge_method}`...\n"
        f"Repo: `{GITHUB_REPO}` → `{GITHUB_BASE_BRANCH}`\n"
        f"Procesando: `#{prs[0]['number']}`..."
    )

    ok_list = []
    fail_list = []

    async def _update_status(i: int, current_num: int | None = None, done: bool = False):
        if done:
            return
        cur = f"\n🔄 Ahora: `#{current_num}`" if current_num else ""
        try:
            await status_msg.edit(
                content=(
                    f"⏳ Progreso: `{i}/{len(prs)}`{cur}\n"
                    f"✅ OK: {len(ok_list)} | ❌ Fallidos: {len(fail_list)}"
                )
            )
        except Exception as e:
            logger.warning(f"[merge_all] No se pudo editar progreso: {e}")

    for i, pr in enumerate(prs, start=1):
        num = pr["number"]
        title = (pr.get("title") or f"PR #{num}")[:50]

        await _update_status(i - 1, current_num=num)

        def _do_merge(n=num, t=title):
            return merge_pull_request(
                n,
                merge_method=merge_method,
                commit_title=f"{t} (#{n})",
            )

        try:
            # Tope duro por PR: evita que un hang de red deje el comando pegado
            success, detail = await asyncio.wait_for(
                asyncio.to_thread(_do_merge),
                timeout=70,
            )
        except asyncio.TimeoutError:
            success, detail = False, "Timeout global 70s en este PR"
        except Exception as e:
            success, detail = False, f"Excepción: {type(e).__name__}: {e}"

        if success:
            ok_list.append(f"#{num}")
            logger.info(f"[merge_all] OK #{num}: {detail}")
        else:
            fail_list.append(f"#{num}: {detail}")
            logger.warning(f"[merge_all] FAIL #{num}: {detail}")

        await _update_status(i)

        # Pausa un poco más generosa: menos rate-limit de GitHub tras muchos merges
        await asyncio.sleep(1.0)

    resumen = [
        f"## Resultado `/merge_all`",
        f"- Repo: `{GITHUB_REPO}` → `{GITHUB_BASE_BRANCH}`",
        f"- Método: `{merge_method}`",
        f"- ✅ Mergeados: **{len(ok_list)}**",
        f"- ❌ Fallidos: **{len(fail_list)}**",
    ]
    if ok_list:
        resumen.append(
            f"\n**OK:** {', '.join(ok_list[:40])}" + ("…" if len(ok_list) > 40 else "")
        )
    if fail_list:
        fails_txt = "\n".join(f"• {f}" for f in fail_list[:15])
        resumen.append(f"\n**Fallos:**\n{fails_txt}")
        if len(fail_list) > 15:
            resumen.append(f"*... y {len(fail_list) - 15} más.*")
    resumen.append(
        "\n💡 Si se cortó a mitad, vuelve a ejecutar `/merge_all` "
        "(solo intentará los que sigan abiertos).\n"
        "Tras el deploy de `main`, usa `/recargar` en el bot."
    )

    final = "\n".join(resumen)
    if len(final) > 2000:
        final = final[:1990] + "\n..."
    try:
        await status_msg.edit(content=final)
    except Exception:
        try:
            await interaction.followup.send(final)
        except Exception as e:
            logger.error(f"[merge_all] No se pudo enviar resumen final: {e}")

# --- ARRANQUE DEL BOT ---

token = os.getenv("DISCORD_TOKEN")
if not token:
    logger.error("ERROR: No se encontró el DISCORD_TOKEN en las variables de entorno.")
else:
    bot.run(token)
