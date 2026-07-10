import discord
from discord import app_commands
from discord.ext import commands
import csv
import os
from pathlib import Path

# Configuración de rutas
BASE_DIR = Path("/app") # Ajusta esto si tu estructura de carpetas es distinta en Railway
CARPETAS = {
    "clasic": BASE_DIR / "Csv_Clasic",
    "ng": BASE_DIR / "Csv_Ngs",
    "ng_r": BASE_DIR / "Csv_Ngs_raw",
    "clasic_r": BASE_DIR / "Csv_Clasic_Raw"
}

TOKEN = os.getenv("DISCORD_TOKEN")

class BuscadorBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        await self.tree.sync()

bot = BuscadorBot()

async def buscar_en_carpeta(categoria: str, query: str):
    ruta = CARPETAS.get(categoria)
    if not ruta or not ruta.exists():
        return ["Error: Carpeta no encontrada."]

    resultados = []
    # Buscamos en todos los CSV de la carpeta seleccionada
    for csv_file in ruta.glob("*.csv"):
        with csv_file.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if query.lower() in row.get("text", "").lower():
                    # Formato: Archivo | ID | Texto
                    resultados.append(f"📄 **{csv_file.name}** | ID: `{row['id']}`\n> {row['text'][:80]}")
                if len(resultados) >= 5: break # Límite para no saturar Discord
        if len(resultados) >= 5: break
    
    return resultados if resultados else ["No se encontraron coincidencias."]

@bot.tree.command(name="buscar", description="Busca texto en las categorías")
@app_commands.describe(
    categoria="Elige donde buscar",
    query="Texto a buscar"
)
@app_commands.choices(categoria=[
    app_commands.Choice(name="Clasic", value="clasic"),
    app_commands.Choice(name="NGS", value="ng"),
    app_commands.Choice(name="NGS Raw", value="ng_r"),
    app_commands.Choice(name="Clasic Raw", value="clasic_r"),
])
async def buscar(interaction: discord.Interaction, categoria: str, query: str):
    await interaction.response.defer()
    res = await buscar_en_carpeta(categoria, query)
    await interaction.followup.send("\n\n".join(res))

if __name__ == "__main__":
    bot.run(TOKEN)