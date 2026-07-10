import discord
from discord import app_commands
from discord.ext import commands
import csv
import os
from pathlib import Path

# Mapeo de carpetas
CARPETAS = {
    "clasic": Path("Csv_Clasic"),
    "ng": Path("Csv_Ngs"),
    "ng_r": Path("Csv_Ngs_raw"),
    "clasic_r": Path("Csv_Clasic_Raw")
}

class BuscadorBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        await self.tree.sync()

bot = BuscadorBot()

@bot.tree.command(name="buscar", description="Busca texto en una categoría")
@app_commands.describe(categoria="Categoría donde buscar", query="Texto a encontrar")
@app_commands.choices(categoria=[
    app_commands.Choice(name="Clasic", value="clasic"),
    app_commands.Choice(name="NGS", value="ng"),
    app_commands.Choice(name="NGS Raw", value="ng_r"),
    app_commands.Choice(name="Clasic Raw", value="clasic_r"),
])
async def buscar(interaction: discord.Interaction, categoria: str, query: str):
    await interaction.response.defer()
    ruta = CARPETAS[categoria]
    resultados = []
    
    for csv_file in ruta.glob("*.csv"):
        with csv_file.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if query.lower() in row.get("text", "").lower():
                    resultados.append(f"📁 **{csv_file.name}** | ID: `{row['id']}`\n> {row['text'][:50]}")
                    if len(resultados) >= 5: break
        if len(resultados) >= 5: break
    
    await interaction.followup.send("\n\n".join(resultados) if resultados else "No se encontraron coincidencias.")

bot.run(os.getenv("DISCORD_TOKEN"))