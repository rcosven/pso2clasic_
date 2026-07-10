import discord
from discord import app_commands
from discord.ext import commands
import os

class BuscadorBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())

    async def setup_hook(self):
        # 1. Borramos comandos previos de Discord para limpiar caché
        await self.tree.clear_commands(guild=None)
        
        # 2. Registramos el comando nuevamente
        print("Registrando comando /buscar...")
        await self.tree.sync()
        print("Sincronización forzada completada.")

bot = BuscadorBot()

# Definición del comando
@bot.tree.command(name="buscar", description="Busca texto en una categoría")
@app_commands.describe(categoria="Categoría", query="Texto")
async def buscar(interaction: discord.Interaction, categoria: str, query: str):
    await interaction.response.send_message("Buscando...")

bot.run(os.getenv("DISCORD_TOKEN"))