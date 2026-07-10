import discord
from discord import app_commands
from discord.ext import commands
import csv
import os
from pathlib import Path

# Asegúrate de que las rutas sean absolutas dentro del contenedor
CARPETAS = {
    "clasic": Path("/app/Csv_Clasic"),
    "ng": Path("/app/Csv_Ngs"),
    "ng_r": Path("/app/Csv_Ngs_raw"),
    "clasic_r": Path("/app/Csv_Clasic_Raw")
}

class BuscadorBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        print("--- Iniciando Sincronización ---")
        # Borramos comandos viejos primero para evitar conflictos
        self.tree.clear_commands(guild=None)
        synced = await self.tree.sync()
        print(f"--- Sincronización completa. Comandos registrados: {len(synced)} ---")

bot = BuscadorBot()

@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")

@bot.tree.command(name="buscar", description="Busca texto en una categoría")
@app_commands.choices(categoria=[
    app_commands.Choice(name="Clasic", value="clasic"),
    app_commands.Choice(name="NGS", value="ng"),
    app_commands.Choice(name="NGS Raw", value="ng_r"),
    app_commands.Choice(name="Clasic Raw", value="clasic_r"),
])
async def buscar(interaction: discord.Interaction, categoria: str, query: str):
    await interaction.response.defer()
    ruta = CARPETAS.get(categoria)
    
    if not ruta or not ruta.exists():
        await interaction.followup.send(f"Error: La carpeta {categoria} no existe.")
        return

    resultados = []
    for csv_file in ruta.glob("*.csv"):
        try:
            with csv_file.open(encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if query.lower() in row.get("text", "").lower():
                        resultados.append(f"📁 **{csv_file.name}** | ID: `{row['id']}`\n> {row['text'][:50]}")
                        if len(resultados) >= 5: break
            if len(resultados) >= 5: break
        except Exception as e:
            continue
    
    await interaction.followup.send("\n\n".join(resultados) if resultados else "No se encontraron coincidencias.")

# Ejecución
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN no configurado")
    else:
        bot.run(token)