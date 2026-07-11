import discord
from discord import app_commands
from discord.ext import commands
import os
import csv
import logging
from pathlib import Path
import base64
import requests
import time
import json
from aiohttp import web

# Configurar variables de GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "rcosven/pso2clasic_")
GITHUB_BASE_BRANCH = os.getenv("GITHUB_BASE_BRANCH", "main")

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

        # 3. Conjunto para rastrear archivos modificados localmente
        self.modified_files = set() 

    async def setup_hook(self):
        self.cargar_indices()
        
        # 0. Arrancar servidor web de inmediato
        try:
            await start_web_server(self)
        except Exception as e:
            logger.error(f"Error al iniciar el servidor web: {e}")
        
        # 1. Sincronización instantánea en tu servidor de pruebas
        mi_servidor = discord.Object(id=1525057654446100553) 
        try:
            self.tree.copy_global_to(guild=mi_servidor)
            logger.info("Sincronizando comandos de barra en el servidor de pruebas...")
            await self.tree.sync(guild=mi_servidor)
        except Exception as e:
            logger.error(f"Error al sincronizar comandos en el servidor de pruebas: {e}")
        
        # 2. Sincronización global para los demás servidores
        try:
            logger.info("Sincronizando comandos globalmente (puede tardar hasta 1 hora en propagarse)...")
            await self.tree.sync()
        except Exception as e:
            logger.error(f"Error al sincronizar comandos globalmente: {e}")
            
        logger.info("Sincronización completada.")

    def cargar_indices(self):
        """Lee los CSV locales y guarda los IDs y textos para búsquedas."""
        self.index_datos.clear()
        
        # Agrega aquí los nombres de las carpetas que contienen tus CSV
        directorios_datos = ["Csv_Clasic", "Csv_Ngs", "Csv_Ngs_Raw", "Csv_Clasic_Raw"]
        
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
                                    self.index_datos.append({
                                        'section': row.get('section', ''),
                                        'group': row.get('group', ''),
                                        'id': row['id'],
                                        'text': row.get('text', ''),
                                        'file': f"{dir_name}/{archivo_csv.name}",
                                        'line': reader.line_num
                                    })
                    except Exception as e:
                        logger.error(f"Error leyendo {archivo_csv.name}: {e}")
            else:
                logger.warning(f"Advertencia: No se encontró la carpeta {dir_name}")
                
        logger.info(f"Índices cargados correctamente. Total de IDs: {len(self.index_datos)}")

def modificar_texto_csv(file_path: str, section: str, group: str, row_id: str, nuevo_texto: str):
    """
    Lee un archivo CSV, busca la fila exacta por section, group e id, 
    y reemplaza únicamente el campo 'text' conservando la estructura multilínea.
    Solo permite modificar si group == '1'.
    """
    if group != '1':
        return False

    ruta = Path(file_path)
    if not ruta.exists():
        return False

    filas = []
    headers = []
    modificado = False

    with open(ruta, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        for row in reader:
            if row.get('section') == section and row.get('group') == group and row.get('id') == row_id:
                row['text'] = nuevo_texto
                modificado = True
            filas.append(row)

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

    # 6. Crear el Pull Request
    payload_pr = {
        "title": f"📝 Sugerencia de traducción: {row_id} por @{usuario_discord}",
        "head": nombre_rama,
        "base": GITHUB_BASE_BRANCH,
        "body": (
            f"El usuario de Discord **@{usuario_discord}** ha sugerido una traducción para el ID `{row_id}` "
            f"en el archivo `{ruta_archivo_repo}`.\n\n"
            f"Por favor, revisa los cambios antes de fusionar."
        )
    }
    res = requests.post(f"{base_url}/pulls", headers=headers_api, json=payload_pr)
    if res.status_code == 201:
        return res.json()["html_url"], None
    else:
        return None, f"Error al crear Pull Request: {res.text}"

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
        f"🌐 **TRADUCTOR VISUAL WEB (Haz clic aquí para traducir de forma fácil):**",
        f"➡️ **http://localhost:8080/edit?file={filepath}** ⬅️",
        f"",
        f"*(El enlace abrirá una interfaz con todas las líneas listas para hacer clic y editar en tiempo real)*"
    ]
    return "\n".join(mensaje_lineas)

class TranslationModal(discord.ui.Modal, title="Sugerir Traducción"):
    def __init__(self, bot_instance, item_data: dict, original_text: str):
        super().__init__()
        self.bot = bot_instance
        self.item = item_data
        
        # Caja de texto para el japonés original (de referencia, explícitamente lectura)
        self.original_input = discord.ui.TextInput(
            label="⚠️ LECTURA - Original (Se ignorará si editas)",
            style=discord.TextStyle.paragraph,
            default=original_text if original_text else "*(Sin original)*",
            required=False
        )
        
        # Caja de texto para el español traducido (editable)
        self.translation_input = discord.ui.TextInput(
            label="Texto traducido",
            style=discord.TextStyle.paragraph,
            default=item_data.get('text', ''),
            required=True
        )
        
        self.add_item(self.original_input)
        self.add_item(self.translation_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Limpiar y sanitizar texto: Reemplazar saltos de línea con <br>
        nuevo_texto = self.translation_input.value.strip()
        nuevo_texto = nuevo_texto.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
        
        file_path_local = self.item['file']
        
        # 1. Modificar CSV local
        exito = modificar_texto_csv(
            file_path=file_path_local,
            section=self.item.get('section', ''),
            group=self.item.get('group', ''),
            row_id=self.item['id'],
            nuevo_texto=nuevo_texto
        )
        
        if not exito:
            await interaction.followup.send("❌ Error: No se pudo modificar el archivo CSV local.", ephemeral=True)
            return

        # 2. Actualizar el índice en memoria para que esté disponible inmediatamente
        for indexed_item in self.bot.index_datos:
            if (indexed_item.get('section') == self.item.get('section') and
                indexed_item.get('group') == self.item.get('group') and
                indexed_item.get('id') == self.item['id'] and
                indexed_item.get('file') == self.item['file']):
                indexed_item['text'] = nuevo_texto
                break

        # 3. Registrar el archivo como modificado para el PR acumulativo
        self.bot.modified_files.add(file_path_local)

        await interaction.followup.send(
            f"✅ **Traducción guardada localmente en `{file_path_local}`.**\n"
            f"Puedes seguir traduciendo otras líneas. Cuando termines, presiona el botón **'Subir a GitHub'** para enviar todas las sugerencias de este archivo juntas.",
            ephemeral=True
        )

class DescargarCSVView(discord.ui.View):
    def __init__(self, bot_instance, filepath: str):
        super().__init__(timeout=180)
        self.bot = bot_instance
        self.filepath = filepath

    @discord.ui.button(label="Descargar CSV", style=discord.ButtonStyle.primary, emoji="📥", row=0)
    async def descargar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            archivo = discord.File(self.filepath)
            await interaction.response.send_message(
                content=f"Aquí tienes el archivo `{self.filepath}` listo para editar:",
                file=archivo,
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                content=f"❌ No se pudo enviar el archivo: {e}",
                ephemeral=True
            )

    @discord.ui.button(label="Subir a GitHub", style=discord.ButtonStyle.primary, emoji="📤", row=0)
    async def subir_github(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        # 1. Comprobar si hay cambios locales
        if self.filepath not in self.bot.modified_files:
            await interaction.followup.send(
                "⚠️ No se han registrado traducciones nuevas en este archivo durante esta sesión, "
                "pero intentaré subirlo de todos modos por si realizaste cambios anteriormente.",
                ephemeral=True
            )
            
        # 2. Generar Pull Request acumulativo
        pr_url, error = crear_pull_request_traduccion(
            ruta_archivo_local=self.filepath,
            ruta_archivo_repo=self.filepath,
            row_id="BatchUpdate",
            usuario_discord=interaction.user.name
        )
        
        if error:
            await interaction.followup.send(f"❌ Error al subir cambios a GitHub: {error}", ephemeral=True)
        else:
            # 3. Limpiar estado de modificado
            if self.filepath in self.bot.modified_files:
                self.bot.modified_files.remove(self.filepath)
                
            await interaction.followup.send(
                f"✅ **¡Archivo subido a GitHub con éxito!**\n"
                f"Se ha creado un Pull Request con todos los cambios acumulados de este archivo:\n🔗 <{pr_url}>",
                ephemeral=True
            )

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
async def web_index(request):
    try:
        with open("web_ui.html", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
    except Exception as e:
        return web.Response(text=f"Error al cargar UI: {e}", status=500)

async def web_api_file(request):
    filename = request.query.get("name")
    if not filename:
        return web.json_response({"error": "Falta parámetro 'name'"}, status=400)
        
    bot = request.app['bot']
    items = []
    
    group_1_items = [item for item in bot.index_datos if item['file'] == filename and item.get('group') == '1']
    
    for g1 in group_1_items:
        original_text = ""
        for g0 in bot.index_datos:
            if g0['file'] == filename and g0.get('section') == g1.get('section') and g0['id'] == g1['id'] and g0.get('group') == '0':
                original_text = g0.get('text', '')
                break
        
        items.append({
            'section': g1.get('section', ''),
            'id': g1['id'],
            'text': g1.get('text', ''),
            'original': original_text
        })
        
    return web.json_response({"items": items})

async def web_api_save(request):
    try:
        data = await request.json()
        filename = data.get('file')
        section = data.get('section', '')
        row_id = data.get('id')
        new_text = data.get('text', '')
        
        bot = request.app['bot']
        
        exito = modificar_texto_csv(filename, section, '1', row_id, new_text)
        if not exito:
            return web.json_response({"error": "No se pudo modificar CSV"}, status=500)
            
        for item in bot.index_datos:
            if item['file'] == filename and item.get('section') == section and item['id'] == row_id and item.get('group') == '1':
                item['text'] = new_text
                break
                
        bot.modified_files.add(filename)
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def web_api_github(request):
    try:
        data = await request.json()
        filename = data.get('file')
        bot = request.app['bot']
        
        pr_url, error = crear_pull_request_traduccion(
            ruta_archivo_local=filename,
            ruta_archivo_repo=filename,
            row_id="WebUpdate",
            usuario_discord="TraductorWeb"
        )
        
        if error:
            return web.json_response({"error": error}, status=500)
            
        if filename in bot.modified_files:
            bot.modified_files.remove(filename)
            
        return web.json_response({"success": True, "url": pr_url})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def start_web_server(bot):
    app = web.Application()
    app['bot'] = bot
    app.router.add_get('/edit', web_index)
    app.router.add_get('/api/file', web_api_file)
    app.router.add_post('/api/save', web_api_save)
    app.router.add_post('/api/github', web_api_github)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("Servidor Web (Traductor Visual) iniciado en http://localhost:8080")

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
    bot.cargar_indices()
    await interaction.response.send_message(f"🔄 Datos recargados con éxito. IDs mapeados: {len(bot.index_datos)}")

# --- ARRANQUE DEL BOT ---

token = os.getenv("DISCORD_TOKEN")
if not token:
    logger.error("ERROR: No se encontró el DISCORD_TOKEN en las variables de entorno.")
else:
    bot.run(token)