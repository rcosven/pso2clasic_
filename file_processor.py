import csv
import shutil
from pathlib import Path
from io import StringIO
from config import REPO_DIR, CSV_DIR, QUARANTINE_DIR
from utils import log, detect_language, should_skip_translate
from github_manager import push_to_github
from ai_translator import batch_translate

def load_raw_reference(base_name: str) -> dict:
    raw_file = None
    for txt_path in REPO_DIR.rglob(f"{base_name}*.txt"):
        raw_file = txt_path
        break
    if not raw_file: return {}
        
    data = {}
    current_section = ""
    current_group = ""
    
    try:
        lines = raw_file.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line or "was created:" in line or "Filesize is:" in line: continue
                
            if line.startswith("Group "):
                current_group = line.replace("Group ", "").strip()
            elif " - " in line and current_section:
                parts = line.split(" - ", 1)
                if len(parts) == 2:
                    id_val = parts[0].strip()
                    text_val = parts[1].strip()
                    if id_val not in data: data[id_val] = {"section": current_section, "0": "", "1": ""}
                    data[id_val][current_group] = text_val
            else:
                current_section = line 
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

        base_name = file_path.stem 
        raw_data = load_raw_reference(base_name)

        fixed_rows = []
        for r in rows:
            if len(r) >= 3 and r[0] != "section":
                id_val = r[2]
                if id_val in raw_data:
                    if r[0].strip() == "": r[0] = raw_data[id_val]["section"]
                    if r[1] == "1" and raw_data[id_val]["1"]:
                        while len(r) < 4: r.append("")
                        # Solo escribimos si r[3] está vacío o solo contiene espacios
                        if not r[3].strip(): 
                            r[3] = raw_data[id_val]["1"]
            fixed_rows.append(r)
        rows = fixed_rows

        malformed_rows = [r for r in rows if len(r) < 4 or r[0].strip() == ""]
        if malformed_rows:
            log(f"🚨 AISLADO: Archivo {file_path.name} movido a Cuarentena.")
            shutil.move(str(file_path), str(QUARANTINE_DIR / file_path.name))
            files_modified += 1
            continue 

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
