import os
import subprocess
import discord
from discord.ext import commands
from pathlib import Path

# === CONFIGURACIÓN ===
TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_URL = "https://github.com/rcosven/pso2clasic_.git"
BASE_DIR = Path("/app/repos_bot") if Path("/app").exists() else Path("./repos_bot")
REPO_PATH = BASE_DIR / "repo_unificado"

# Configuración de permisos para leer mensajes
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

def setup_repo():
    """Clona o actualiza el repositorio único para la búsqueda."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    
    if not REPO_PATH.exists():
        print(f"Clonando repositorio unificado en {REPO_PATH}...")
        subprocess.run(["git", "clone", "-b", "main", "--single-branch", GITHUB_URL, str(REPO_PATH)], check=True)
    else:
        print(f"Actualizando repositorio unificado en {REPO_PATH}...")
        subprocess.run(["git", "pull"], cwd=REPO_PATH, check=False)

def buscar_texto(carpeta_destino: str, query: str) -> str:
    """Busca el texto usando git grep dentro de una carpeta específica."""
    # Actualizar antes de buscar para tener los CSV más recientes
    subprocess.run(["git", "pull"], cwd=REPO_PATH, capture_output=True)

    resultado = subprocess.run(
        ["git", "grep", "-i", "-n", query, "--", carpeta_destino], 
        cwd=REPO_PATH, 
        capture_output=True, 
        text=True
    )
    
    if not resultado.stdout.strip():
        return f"❌ No se encontró `{query}` en la carpeta **{carpeta_destino}**."

    lineas = resultado.stdout.strip().split('\n')
    respuesta = f"🔍 **Resultados en {carpeta_destino} para:** `{query}`\n\n"
    
    for linea in lineas[:10]:
        # Formato esperado: Carpeta/archivo.csv:numero:texto
        partes = linea.split(':', 2)
        if len(partes) >= 2:
            # Limpiamos el nombre de la carpeta para que sea más fácil de leer
            archivo = partes[0].replace(f"{carpeta_destino}/", "")
            num_linea = partes[1]
            respuesta += f"📄 **{archivo}** (Línea {num_linea})\n"
            
    if len(lineas) > 10:
        respuesta += f"\n*...y {len(lineas) - 10} resultados más.*"
        
    return respuesta

@bot.event
async def on_ready():
    print(f'✅ Bot conectado como {bot.user}')
    setup_repo()
    print("✅ Repositorio unificado listo. Esperando comandos...")

# === COMANDOS ===

@bot.command(name='en')
async def buscar_en(ctx, *, query: str):
    mensaje = await ctx.send(f"Buscando `{query}` en **Csv_Ngs**... ⏳")
    resultado = buscar_texto("Csv_Ngs", query)
    await mensaje.edit(content=resultado)

@bot.command(name='en_r')
async def buscar_en_raw(ctx, *, query: str):
    mensaje = await ctx.send(f"Buscando `{query}` en **Csv_Ngs_Raw**... ⏳")
    resultado = buscar_texto("Csv_Ngs_Raw", query)
    await mensaje.edit(content=resultado)

@bot.command(name='es')
async def buscar_es(ctx, *, query: str):
    mensaje = await ctx.send(f"Buscando `{query}` en **Csv_Clasic**... ⏳")
    resultado = buscar_texto("Csv_Clasic", query)
    await mensaje.edit(content=resultado)

@bot.command(name='es_r')
async def buscar_es_raw(ctx, *, query: str):
    mensaje = await ctx.send(f"Buscando `{query}` en **Csv_Clasic_Raw**... ⏳")
    resultado = buscar_texto("Csv_Clasic_Raw", query)
    await mensaje.edit(content=resultado)

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No se encontró la variable DISCORD_TOKEN.")
    else:
        bot.run(TOKEN)
