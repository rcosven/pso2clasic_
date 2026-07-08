import os
from pathlib import Path

# === RUTAS DEL PROYECTO ===
REPO_DIR = Path("/app")
CSV_DIR = REPO_DIR / "archivos a traducir"
LISTO_DIR = REPO_DIR / "listo"
QUARANTINE_DIR = REPO_DIR / "Cuarentena"
CACHE_DB = Path("/app/translate_cache.db")
LOG = Path("/app/translate_missing.log")

# === CONFIGURACIÓN DE GITHUB ===
GITHUB_REPO = os.getenv("GITHUB_REPO", "rcosven/pso2clasic_")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

TIEMPO_ESPERA = 600

# === DICCIONARIO DE ETIQUETAS ===
TAG_FIXES = {
    "<amarillo>": "<yellow>", "</amarillo>": "</yellow>",
    "<rojo>": "<red>", "</rojo>": "</red>",
    "<verde>": "<green>", "</verde>": "</green>",
    "<azul>": "<blue>", "</azul>": "</blue>",
    "<rosa>": "<pink>", "</rosa>": "</pink>",
    "<morado>": "<purple>", "</morado>": "</purple>",
}
