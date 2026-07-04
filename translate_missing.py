#!/usr/bin/env python3
"""
PSO2 CSV Spanish Translator - Ciclo Continuo con IA Integrada (Fallback)
- Hace Pull constante desde GitHub.
- Traduce el group 1 usando Gemini (con Llama 3 como respaldo si falla).
- Respeta etiquetas de código y glosario.
- Hace Push intermitente (cada 30 archivos modificados) para evitar pérdidas.
- No depende de volúmenes persistentes.
"""

import os
import re
import csv
import sqlite3
import time
import subprocess
import json
from pathlib import Path
from io import StringIO

import litellm
from litellm import completion

# Ocultar advertencias molestas de la consola
litellm.suppress_debug_info = True 

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    HAS_LANGDETECT = True
except ImportError:
    HAS_LANGDETECT = False

# === RUTAS DEL PROYECTO NUEVO ===
REPO_DIR = Path("/app")
CSV_DIR = REPO_DIR / "archivos_extraidos"
CACHE_DB = Path("/app/translate_cache.db")
LOG = Path("/app/translate_missing.log")

# === CONFIGURACIÓN DE TU GITHUB ===
GITHUB_REPO = os.getenv("GITHUB_REPO", "tu_usuario/pso2clasic")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
TIEMPO_ESPERA = 600 # 10 minutos entre cada revisión

TAG_FIXES = {
    "<amarillo>": "<yellow>", "</amarillo>": "</yellow>",
    "<rojo>": "<red>", "</rojo>": "</red>",
    "<verde>": "<green>", "</verde>": "</green>",
    "<azul>": "<blue>", "</azul>": "</blue>",
    "<rosa>": "<pink>", "</rosa>": "</pink>",
    "<morado>": "<purple>", "</morado>": "</purple>",
}

def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def fix_broken_tags():
    fixed_count = 0
    if not CSV_DIR.exists():
        return
    for csv_file in CSV_DIR.rglob("*.csv"):
        try:
            content = csv_file.read_text(encoding="utf-8")
            new_content = content
            for bad, good in TAG_FIXES.items():
                if bad in new_content:
                    new_content = new_content.replace(bad, good)
                    fixed_count += 1
            if new_content != content:
                csv_file.write_text(new_content, encoding="utf-8")
        except:
            pass
    if fixed_count > 0:
        log(f"Se arreglaron {fixed_count} tags rotos.")

def apply_release_sed(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    repl = {
        "á": "a", "à": "a", "è": "e", "é": "e", "ì": "i", "í": "i",
        "ò": "o", "ó": "o", "ö": "o", "ō": "o", "ù": "u", "ú": "u", "ü": "u",
        "ñ": "й", "Á": "A", "À": "A", "È": "E", "É": "E", "Ì": "I", "Í": "I",
        "Ò": "O", "Ó": "O", "Ö": "O", "Ō": "O", "Ù": "U", "Ú": "U", "Ü": "U", "Ñ": "Й",
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    return text

def init_cache():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (src TEXT, lang TEXT, dst TEXT, PRIMARY KEY(src, lang))")
    conn.commit()
    return conn

def should_skip_translate(text: str) -> bool:
    if not text or not text.strip(): return True
    if "<" in text and ">" in text: return True
    if text.startswith("<&"): return True
    if "((" in text or "intextor" in text.lower(): return True
    return False

def detect_language(text: str) -> str:
    if not HAS_LANGDETECT: return "en"
    try: return detect(text)
    except Exception: return "en"

def setup_git():
    token = os.getenv("GITHUB_TOKEN")
    if not token or GITHUB_REPO == "tu_usuario/pso2clasic":
        log("ERROR CRÍTICO: GITHUB_TOKEN o GITHUB_REPO no configurados en Railway.")
        return False
        
    log(f"Configurando Git para {GITHUB_REPO} en rama {GITHUB_BRANCH}...")
    
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)

    if not (REPO_DIR / ".git").exists():
        log("Reconstruyendo entorno Git interno...")
        subprocess.run(["git", "init"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "branch", "-m", GITHUB_BRANCH], cwd=REPO_DIR, check=False)
    
    remote_url = f"https://oauth2:{token}@github.com/{GITHUB_REPO}.git"
    
    res = subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=REPO_DIR, check=False)
    if res.returncode != 0:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)

    subprocess.run(["git", "fetch", "origin"], cwd=REPO_DIR, check=False)
    subprocess.run(["git", "reset", "--mixed", f"origin/{GITHUB_BRANCH}"], cwd=REPO_DIR, check=False)

    return True

def pull_from_github():
    log("=== Descargando novedades de GitHub (Pull) ===")
    res = subprocess.run(["git", "pull", "origin", GITHUB_BRANCH], cwd=REPO_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"Advertencia en Pull: {res.stderr.strip()}")
    else:
        out = res.stdout.strip()
        if "Already up to date" in out:
            log("No hay archivos nuevos en GitHub.")
        else:
            log("Nuevos archivos descargados correctamente.")

def push_to_github():
    if not CSV_DIR.exists(): return
    subprocess.run(["git", "add", "archivos_extraidos/"], cwd=REPO_DIR, check=False)
        
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip():
        log("No hay traducciones nuevas para subir a GitHub.")
        return
        
    log("Guardando cambios en GitHub (Push)...")
    subprocess.run(["git", "commit", "-m", "Bot: Auto-traduccion CSV ES actualizada"], cwd=REPO_DIR, check=False)
    push = subprocess.run(["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"], cwd=REPO_DIR, capture_output=True, text=True)
    if push.returncode == 0:
        log("Cambios subidos OK")
    else:
        log(f"Error push: {push.stderr[:150]}")

# === NUEVO MOTOR DE TRADUCCIÓN CON LITELLM ===
def batch_translate(conn, texts: list[str], src_lang: str) -> dict[str, str]:
    out_map: dict[str, str] = {}
    pending: list[str] = []
    
    for text in texts:
        if should_skip_translate(text):
            out_map[text] = text
            continue
        row = conn.execute("SELECT dst FROM cache WHERE src=? AND lang=?", (text, src_lang)).fetchone()
        if row: 
            out_map[text] = row[0]
        else: 
            pending.append(text)
        
    if not pending: return out_map
    
    # Procesar en lotes de 20 para no saturar la IA y asegurar que el JSON no se rompa
    chunk_size = 20
    
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i:i + chunk_size]
        chunk_dict = {str(idx): text for idx, text in enumerate(chunk)}
        
        prompt = f"""Eres un traductor profesional de videojuegos trabajando en Phantasy Star Online 2 (PSO2).
Traduce los valores del siguiente objeto JSON de Inglés a Español.

REGLAS ESTRICTAS:
1. NUNCA traduzcas las etiquetas de color o formato (ej. <yellow>, <red>, \n).
2. NUNCA traduzcas nombres de variables entre corchetes o llaves (ej. {{player_name}}, [Target]).
3. GLOSARIO: Mantén estas palabras en inglés: Arks, Falspawn, Monomate, Photon.
4. Tono: Épico pero natural.
5. DEBES devolver ÚNICAMENTE un objeto JSON válido con las mismas claves numéricas.

JSON A TRADUCIR:
{json.dumps(chunk_dict, ensure_ascii=False)}
"""

        translated_dict = {}
        try:
            log(f"Traduciendo lote de {len(chunk)} textos con IA...")
            response = completion(
                model="gemini/gemini-1.5-flash-latest", 
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, 
                fallbacks=["groq/llama-3.1-8b-instant"], # <-- Nuevo modelo de Groq
                num_retries=2
            )
            
            result_text = response.choices[0].message.content
            translated_dict = json.loads(result_text)
            
        except Exception as e:
            log(f"Error en IA o formato JSON inválido. Saltando lote: {str(e)[:100]}")
            # Si hay error, asignamos el texto original para que el script no se caiga
            translated_dict = {str(idx): text for idx, text in enumerate(chunk)}

        for idx_str, src in chunk_dict.items():
            dst = translated_dict.get(idx_str, src)
            dst = apply_release_sed(dst)
            out_map[src] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src, src_lang, dst))
            
        conn.commit()
        time.sleep(2) # Pausa obligatoria para evitar baneos por spam (Rate Limit)
        
    return out_map

def read_csv_rows(path: Path) -> list[list[str]]:
    rows = []
    if not path.exists(): return rows
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader: rows.append(row)
    return rows

def process_translations(conn, git_ready):
    files_to_process = list(CSV_DIR.rglob("*.csv"))
    files_modified = 0

    for file_path in files_to_process:
        rows = read_csv_rows(file_path)
        if not rows: continue

        original_content = ""
        try: original_content = file_path.read_text(encoding="utf-8")
        except: pass

        texts_en = []
        row_meta = []

        for row in rows:
            if len(row) < 4 or row[0] == "section":
                row_meta.append((row, "", False))
                continue
            
            group_id = row[1]
            text = row[3]
            needs = False
            
            if group_id == "1" and not should_skip_translate(text):
                if detect_language(text) != "es":
                    needs = True
                    texts_en.append(text)
                
            row_meta.append((row, text, needs))

        trans_en = batch_translate(conn, sorted(set(texts_en)), "en") if texts_en else {}

        new_rows = []
        for i, item in enumerate(row_meta):
            original_row = rows[i]
            if len(original_row) < 4 or original_row[0] == "section":
                new_rows.append(original_row)
                continue
                
            row_data, text, needs = item
            translated = trans_en.get(text, text) if needs else text
                
            new_row = list(original_row)
            new_row[3] = translated
            new_rows.append(new_row)

        output = StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerows(new_rows)
        new_content = output.getvalue()

        if new_content != original_content:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
            files_modified += 1
            log(f"Traducido: {file_path.name}")
            
            # === GUARDADO INTERMITENTE (Cada 30 archivos modificados) ===
            if git_ready and files_modified % 30 == 0:
                log(f"Progreso alcanzado ({files_modified} archivos). Asegurando cambios en GitHub...")
                push_to_github()

    # Guardado final de los archivos restantes que no sumaron un múltiplo de 30
    if git_ready and files_modified > 0:
        push_to_github()

def main():
    LOG.write_text("", encoding="utf-8")
    log("=== Iniciando Demonio Traductor PSO2 ES con IA ===")
    
    if not HAS_LANGDETECT:
        log("ERROR: Falta langdetect.")
        return

    git_ready = setup_git()
    conn = init_cache()

    while True:
        if git_ready:
            pull_from_github()
            
        fix_broken_tags()
        process_translations(conn, git_ready)
        
        log(f"Ciclo terminado. Esperando {TIEMPO_ESPERA / 60} minutos...")
        time.sleep(TIEMPO_ESPERA)

if __name__ == "__main__":
    main()
