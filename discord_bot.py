import discord
from discord import app_commands
from discord.ext import commands
import os
import csv
import logging
from pathlib import Path

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

    async def setup_hook(self):
        self.cargar_indices()
        
        # 1. Sincronización instantánea en tu servidor de pruebas
        mi_servidor = discord.Object(id=1525057654446100553) 
        self.tree.copy_global_to(guild=mi_servidor)
        logger.info("Sincronizando comandos de barra en el servidor de pruebas...")
        await self.tree.sync(guild=mi_servidor)
        
        # 2. Sincronización global para los demás servidores
        logger.info("Sincronizando comandos globalmente (puede tardar hasta 1 hora en propagarse)...")
        await self.tree.sync()
        
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

class DescargarCSVView(discord.ui.View):
    def __init__(self, filepath: str):
        super().__init__(timeout=180)
        self.filepath = filepath

    @discord.ui.button(label="Descargar CSV", style=discord.ButtonStyle.primary, emoji="📥")
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

class DescargarDropdown(discord.ui.Select):
    def __init__(self, files: list):
        options = []
        for fpath in files[:25]:
            label = fpath[-100:]
            options.append(discord.SelectOption(label=label, value=fpath, emoji="📁"))
            
        super().__init__(
            placeholder="Elige un archivo para descargar...",
            min_values=1,
            max_values=1,
            options=options
        )
        
    async def callback(self, interaction: discord.Interaction):
        filepath = self.values[0]
        try:
            archivo = discord.File(filepath)
            await interaction.response.send_message(
                content=f"Aquí tienes el archivo `{filepath}` listo para editar:",
                file=archivo,
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                content=f"❌ No se pudo enviar el archivo: {e}",
                ephemeral=True
            )

class DescargarMultipleView(discord.ui.View):
    def __init__(self, files: list):
        super().__init__(timeout=180)
        self.add_item(DescargarDropdown(files))

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
        mensaje = (
            f"✅ **Coincidencia encontrada:**\n"
            f"📁 **Archivo:** `{match['file']}` (Línea {match['line']})\n"
            f"📝 **Texto:** {match['text']}"
        )
        view = DescargarCSVView(match['file'])
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
            view = DescargarCSVView(archivos_unicos[0])
            lineas.append("\n💡 *Usa el botón de abajo para descargar el archivo.*")
        else:
            view = DescargarMultipleView(archivos_unicos)
            lineas.append("\n💡 *Usa la lista desplegable de abajo para descargar cualquiera de los archivos.*")
            if len(archivos_unicos) > 25:
                lineas.append("⚠️ *Hay más de 25 archivos. Se muestran los primeros 25 en la lista desplegable.*")
            
        mensaje = "\n".join(lineas)
        if len(mensaje) > 2000:
            mensaje = mensaje[:1990] + "\n..."
        await interaction.response.send_message(mensaje, view=view)

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