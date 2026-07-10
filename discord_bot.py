import discord
from discord import app_commands
from discord.ext import commands
import os
import csv
from pathlib import Path

class BuscadorBot(commands.Bot):
    def __init__(self):
        # 1. Configurar los Intents
        intents = discord.Intents.default()
        intents.message_content = True 
        
        super().__init__(command_prefix="!", intents=intents)
        
        # 2. Diccionario en memoria para búsquedas rápidas
        self.index_datos = {} 

    async def setup_hook(self):
        self.tree.clear_commands(guild=None) 
        self.cargar_indices()
        
        # --- CAMBIO AQUÍ PARA SINCRONIZACIÓN INSTANTÁNEA ---
        # ¡IMPORTANTE! Reemplaza los números por el ID real de tu servidor de Discord
        mi_servidor = discord.Object(id=123456789012345678) 
        
        # Copiamos los comandos a ese servidor en específico
        self.tree.copy_global_to(guild=mi_servidor)
        
        print("Sincronizando comandos de barra en el servidor...")
        await self.tree.sync(guild=mi_servidor)
        print("Sincronización completada.")

    def cargar_indices(self):
        """Lee los CSV locales y mapea los IDs al archivo correspondiente."""
        self.index_datos.clear()
        
        # Agrega aquí los nombres de las carpetas que contienen tus CSV
        directorios_datos = ["Csv_Clasic", "Csv_Ngs", "Csv_Ngs_raw", "Csv_Clasic_Raw"]
        
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
                                    self.index_datos[row['id']] = archivo_csv.name
                    except Exception as e:
                        print(f"Error leyendo {archivo_csv.name}: {e}")
            else:
                print(f"Advertencia: No se encontró la carpeta {dir_name}")
                
        print(f"Índices cargados correctamente. Total de IDs: {len(self.index_datos)}")

bot = BuscadorBot()

# --- COMANDOS DE BARRA (SLASH COMMANDS) ---

@bot.tree.command(name="buscar_id", description="Busca en qué archivo está un ID específico")
@app_commands.describe(id_buscado="El ID de la línea que deseas encontrar")
async def buscar_id(interaction: discord.Interaction, id_buscado: str):
    archivo = bot.index_datos.get(id_buscado)
    if archivo:
        await interaction.response.send_message(f"✅ El ID **{id_buscado}** se encuentra en el archivo: `{archivo}`")
    else:
        await interaction.response.send_message(f"❌ No se encontró el ID **{id_buscado}** en los registros.")

@bot.tree.command(name="recargar", description="Vuelve a leer los archivos CSV sin reiniciar el bot")
async def recargar(interaction: discord.Interaction):
    bot.cargar_indices()
    await interaction.response.send_message(f"🔄 Datos recargados con éxito. IDs mapeados: {len(bot.index_datos)}")

# --- ARRANQUE DEL BOT ---

token = os.getenv("DISCORD_TOKEN")
if not token:
    print("ERROR: No se encontró el DISCORD_TOKEN en las variables de entorno.")
else:
    bot.run(token)