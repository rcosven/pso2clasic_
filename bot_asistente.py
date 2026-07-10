import os
import subprocess
import discord
from discord.ext import commands
from pathlib import Path

# === CONFIGURACIÓN ===
TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_URL = "https://github.com/rcosven/pso2clasic_.git"
# Usamos el directorio temporal de Railway si está disponible, o una carpeta local
BASE_DIR = Path("/app/repos_bot") if Path("/app").exists() else Path("./repos_bot")

bot = commands.Bot(command_prefix='/', intents=discord.Intents.default())

def setup_repo(folder_name: str, branch: str):
    """Clona o actualiza el repositorio para la búsqueda."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = BASE_DIR / folder_name
    
    if not repo_path.exists():
        print(f"Clonando {branch} en {repo_path}...")
        subprocess.run(["git", "clone", "-b", branch, "--single-branch", GITHUB_URL, str(repo_path)], check=True)
    else:
        print(f"Actualizando {branch} en {repo_path}...")
        subprocess.run(["git", "pull"], cwd=repo_path, check=False)
    return repo_path

def buscar_texto(folder_name: str, query: str) -> str:
    """Busca el texto usando git grep y formatea la salida."""
    repo_path = BASE_DIR / folder_name
    
    # Actualizar antes de buscar para tener los CSV más recientes
    subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True)

    resultado = subprocess.run(
        ["git", "grep", "-i", "-n", query, "--", "listo/"], 
        cwd=repo_path, 
        capture_output=True, 
        text=True
    )
    
    if not resultado.stdout.strip():
        return f"❌ No se encontró `{query}` en esta rama."

    lineas = resultado.stdout.strip().split('\n')
    respuesta = f"🔍 **Resultados para:** `{query}`\n\n"
    
    for linea in lineas[:10]:
        # Formato esperado: listo/archivo.csv:numero:texto
        partes = linea.split(':', 2)
        if len(partes) >= 2:
            archivo = partes[0].replace("listo/", "")
            num_linea = partes[1]
            respuesta += f"📄 **{archivo}** (Línea {num_linea})\n"
            
    if len(lineas) > 10:
        respuesta += f"\n*...y {len(lineas) - 10} resultados más.*"
        
    return respuesta

@bot.event
async def on_ready():
    print(f'✅ Bot conectado como {bot.user}')
    setup_repo("repo_main", "main")
    setup_repo("repo_ngs", "NGs")
    print("✅ Repositorios listos. Esperando comandos...")

@bot.command(name='es')
async def buscar_main(ctx, *, query: str):
    mensaje = await ctx.send(f"Buscando `{query}` en **main**... ⏳")
    resultado = buscar_texto("repo_main", query)
    await mensaje.edit(content=resultado)

@bot.command(name='ng')
async def buscar_ngs(ctx, *, query: str):
    mensaje = await ctx.send(f"Buscando `{query}` en **NGs**... ⏳")
    resultado = buscar_texto("repo_ngs", query)
    await mensaje.edit(content=resultado)

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No se encontró la variable DISCORD_TOKEN.")
    else:
        bot.run(TOKEN)