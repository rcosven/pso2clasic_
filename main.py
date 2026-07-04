#!/usr/bin/env python3
import time
from config import TIEMPO_ESPERA
from utils import log, fix_broken_tags, HAS_LANGDETECT
from github_manager import setup_git, pull_from_github
from ai_translator import init_cache
from file_processor import process_translations

def main():
    # Limpiamos el archivo de log al iniciar
    open("translate_missing.log", "w", encoding="utf-8").close()
    
    log("=== Iniciando Demonio Traductor PSO2 ES (Versión Modular) ===")
    
    if not HAS_LANGDETECT:
        log("ERROR: Falta instalar 'langdetect'. Revisa tu requirements.txt.")
        return

    # Inicialización
    git_ready = setup_git()
    conn = init_cache()

    # Ciclo infinito
    while True:
        if git_ready: 
            pull_from_github()
            
        fix_broken_tags()
        process_translations(conn, git_ready)
        
        log(f"Ciclo terminado. Esperando {TIEMPO_ESPERA / 60} minutos para el siguiente...")
        time.sleep(TIEMPO_ESPERA)

if __name__ == "__main__":
    main()
