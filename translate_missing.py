#!/usr/bin/env python3
"""
PSO2 CSV Spanish Translator - Ciclo Continuo con IA, Contexto, Cuarentena y Raw Reference
- Hace Pull constante desde GitHub.
- Lee archivos Raw (.txt) como 'Fuente de la Verdad' para reparar CSVs y obtener el inglés real.
- Mueve archivos irreparables a la carpeta 'Cuarentena'.
- Traduce el Grupo 1 usando Gemini (con Llama 3.1 como respaldo).
"""

import os
import re
import csv
import sqlite3
import time
import subprocess
import json
import shutil
from pathlib import Path
from io import StringIO

import litellm
from litellm import completion

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
QUARANTINE_DIR = REPO_DIR / "Cuarentena"
CACHE_DB = Path("/app/translate_cache.db")
LOG = Path("/app/translate_missing.log")

GITHUB_REPO = os.getenv("GITHUB_REPO", "tu_usuario/pso2clasic")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
TIEMPO_ESPERA = 600

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
    if not CSV_DIR.exists(): return
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
        except: pass
    if fixed_count > 0:
        log(f"Se arreglaron {fixed_count} tags rotos.")

def apply_release_sed(text: str) -> str:
    if not isinstance(text, str): return str(text)
    repl = {
        "á": "a", "à": "a", "è": "e", "é": "e", "ì": "i", "í": "i",
        "ò": "o", "ó": "o", "ö": "o", "ō": "o", "ù": "u", "ú": "u",
        "ñ": "й", "Á": "A", "À": "A", "È": "E", "É": "E", "Ì": "I", "Í": "I",
        "Ò": "O", "Ó": "O", "Ö": "O", "Ō": "O", "Ù": "U", "Ú": "U", "Ü": "U", "Ñ": "Й",
    }
    for k, v in repl.items(): text = text.replace(k, v)
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
        log("ERROR CRÍTICO: Configura GITHUB_TOKEN o GITHUB_REPO.")
        return False
        
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)

    if not (REPO_DIR / ".git").exists():
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
    res = subprocess.run(["git", "pull", "origin", GITHUB_BRANCH], cwd=REPO_DIR, capture_output=True, text=True)
    out = res.stdout.strip()
    if "Already up to date" not in out and res.returncode == 0:
        log("Nuevos archivos descargados correctamente.")

def push_to_github():
    if not CSV_DIR.exists(): return
    subprocess.run(["git", "add", "archivos_extraidos/"], cwd=REPO_DIR, check=False)
    if QUARANTINE_DIR.exists():
        subprocess.run(["git", "add", "Cuarentena/"], cwd=REPO_DIR, check=False)
        
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip(): return
        
    log("Guardando cambios en GitHub (Push)...")
    subprocess.run(["git", "commit", "-m", "Bot: Traducciones, Raw Healing y Cuarentena"], cwd=REPO_DIR, check=False)
    subprocess.run(["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"], cwd=REPO_DIR, check=False)

def batch_translate(conn, text_data: list[tuple[str, str]], src_lang: str) -> dict[str, str]:
    out_map: dict[str, str] = {}
    pending: list[tuple[str, str]] = []
    
    for text, context in text_data:
        if should_skip_translate(text):
            out_map[text] = text
            continue
        row = conn.execute("SELECT dst FROM cache WHERE src=? AND lang=?", (text, src_lang)).fetchone()
        if row: out_map[text] = row[0]
        else: pending.append((text, context))
        
    if not pending: return out_map
    chunk_size = 15
    
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i:i + chunk_size]
        chunk_dict = {str(idx): {"contexto_original": ctx, "texto_a_traducir": txt} for idx, (txt, ctx) in enumerate(chunk)}
        
        prompt = f"""Eres un traductor profesional de videojuegos (PSO2).
Recibirás un objeto JSON. 'contexto_original' es japonés (NO TRADUCIR, solo para contexto). 'texto_a_traducir' es lo que DEBES traducir al español.
REGLAS:
1. No traduzcas etiquetas HTML/XML ni corchetes.
2. Mantén en inglés: Arks, Falspawn, Monomate, Photon.
3. Devuelve SOLO un JSON con formato: {{"0": "Traduccion1", "1": "Traduccion2"}}

JSON A TRADUCIR:
{json.dumps(chunk_dict, ensure_ascii=False)}
"""
        translated_dict = {}
        try:
            log(f"Traduciendo lote de {len(chunk)} textos...")
            response = completion(
                model="gemini/gemini-2.5-flash", 
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, 
                fallbacks=["groq/llama-3.1-8b-instant"],
                num_retries=2
            )
            translated_dict = json.loads(response.choices[0].message.content)
        except Exception as e:
            log(f"Error en IA. Saltando lote...")
            translated_dict = {str(idx): txt for idx, (txt, ctx) in enumerate(chunk)}

        for idx_str, (src_text, _) in enumerate(chunk):
            idx_str = str(idx_str)
            dst = translated_dict.get(idx_str, src_text)
            if isinstance(dst, dict): dst = dst.get("texto_a_traducir", src_text) 
            dst = apply_release_sed(dst)
            out_map[src_text] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src_text, src_lang, dst))
        conn.commit()
        time.sleep(2) 
    return out_map

# === NUEVA FUNCIÓN: LECTOR DE RAW (.txt) ===
def load_raw_reference(base_name: str) -> dict:
    """Busca un .txt que coincida con el nombre del CSV y extrae la verdad absoluta."""
    raw_file = None
    # Busca en todo el repositorio cualquier .txt que empiece con el nombre del csv
    for txt_path in REPO_DIR.rglob(f"{base_name}*.txt"):
        raw_file = txt_path
        break
        
    if not raw_file:
        return {}
        
    data = {}
    current_section = ""
    current_group = ""
    
    try:
        lines = raw_file.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line or "was created:" in line or "Filesize is:" in line:
                continue
                
            if line.startswith("Group "):
                current_group = line.replace("Group ", "").strip()
            elif " - " in line and current_section:
                parts = line.split(" - ", 1)
                if len(parts) == 2:
                    id_val = parts[0].strip()
                    text_val = parts[1].strip()
                    if id_val not in data:
                        data[id_val] = {"section": current_section, "0": "", "1": ""}
                    data[id_val][current_group] = text_val
            else:
                current_section = line # Es un section header (ej. 'common')
                current_group = ""
    except Exception as e:
        log(f"Advertencia leyendo RAW {raw_file.name}: {e}")
        
    return data

def read_csv_rows(path: Path) -> list[list[str]]:
    rows = []
    if not path.exists(): return rows
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader: rows.append(row)
    return rows

def process_translations(conn, git_ready):
    QUARANTINE_DIR.mkdir(exist_ok=True)
    files_to_process = list(CSV_DIR.rglob("*.csv"))
    files_modified = 0

    for file_path in files_to_process:
        rows = read_csv_rows(file_path)
        if not rows: continue

        # 1. Cargar la Verdad Absoluta (RAW file)
        base_name = file_path.stem # Ej: ii_bonusmap_ahl
        raw_data = load_raw_reference(base_name)

        # 2. Reparar e Inyectar datos reales del RAW al CSV
        fixed_rows = []
        for r in rows:
            if len(r) >= 3 and r[0] != "section":
                id_val = r[2]
                if id_val in raw_data:
                    # REPARACIÓN: Si falta el section, lo inyectamos desde el RAW
                    if r[0].strip() == "":
                        r[0] = raw_data[id_val]["section"]
                    
                    # MEJORA DE TRADUCCIÓN: Si es el texto en inglés (Grupo 1), 
                    # sobreescribimos el CSV con el inglés REAL del RAW para dárselo a la IA.
                    if r[1] == "1" and raw_data[id_val]["1"]:
                        # Aseguramos que la línea tenga 4 columnas para evitar Out of Index
                        while len(r) < 4: r.append("")
                        r[3] = raw_data[id_val]["1"]
            fixed_rows.append(r)
        rows = fixed_rows

        # 3. Validar estrictamente (Si aún después de la reparación sigue roto)
        malformed_rows = [r for r in rows if len(r) < 4 or r[0].strip() == ""]
        if malformed_rows:
            log(f"🚨 AISLADO: Archivo {file_path.name} movido a Cuarentena (Faltan RAWs o está muy roto).")
            destino_cuarentena = QUARANTINE_DIR / file_path.name
            shutil.move(str(file_path), str(destino_cuarentena))
            files_modified += 1
            continue 

        # 4. Extraer el contexto (Grupo 0) para la IA
        context_jp_map = {}
        for row in rows:
            if len(row) >= 4 and row[1] == "0":
                clave_unica = f"{row[0]}_{row[2]}" 
                context_jp_map[clave_unica] = row[3]

        original_content = ""
        try: original_content = file_path.read_text(encoding="utf-8")
        except: pass

        texts_to_translate = []
        row_meta = []

        for row in rows:
            if len(row) < 4 or row[0] == "section":
                row_meta.append((row, "", "", False))
                continue
            
            section = row[0]
            group_id = row[1]
            id_val = row[2]
            text = row[3]
            needs = False
            
            if group_id == "1" and not should_skip_translate(text):
                if detect_language(text) != "es":
                    needs = True
                    contexto_encontrado = context_jp_map.get(f"{section}_{id_val}", "")
                    texts_to_translate.append((text, contexto_encontrado))
                
            row_meta.append((row, text, needs))

        unique_texts = list(dict.fromkeys(texts_to_translate))
        trans_en = batch_translate(conn, unique_texts, "en") if unique_texts else {}

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
            log(f"Traducido exitosamente: {file_path.name}")
            
            if git_ready and files_modified % 30 == 0:
                push_to_github()

    if git_ready and files_modified > 0:
        push_to_github()

def main():
    LOG.write_text("", encoding="utf-8")
    log("=== Iniciando Demonio Traductor PSO2 ES ===")
    if not HAS_LANGDETECT: return
    git_ready = setup_git()
    conn = init_cache()

    while True:
        if git_ready: pull_from_github()
        fix_broken_tags()
        process_translations(conn, git_ready)
        time.sleep(TIEMPO_ESPERA)

if __name__ == "__main__":
    main()
