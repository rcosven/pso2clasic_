import time
from config import LOG, CSV_DIR, TAG_FIXES

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    HAS_LANGDETECT = True
except ImportError:
    HAS_LANGDETECT = False

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
        import re

# Diccionario de correcciones automáticas.
# A la izquierda está la palabra mal traducida (en minúsculas), a la derecha la palabra correcta.
GLOSARIO_ERRORES = {
    r"\bbarcos?\b": "Ship",          # Corrige barco o barcos
    r"\bnave nodriza\b": "Ship",
    r"\bengendros?\b": "Falspawn",    # Corrige engendro o engendros
    r"\bmolinillos?\b": "Grinder",
    r"\bfotón\b": "Photon",
    r"\bfotones\b": "Photons",
    r"\barte fotónico\b": "Photon Art",
    r"\batomizador lunar\b": "Moon Atomizer",
    r"\bexplosión pse\b": "PSE Burst",
}

def forzar_glosario(texto):
    """Fuerza la corrección de palabras clave si la IA se equivocó."""
    texto_corregido = texto
    for error, correccion in GLOSARIO_ERRORES.items():
        # Busca la palabra mal traducida ignorando mayúsculas/minúsculas y la reemplaza
        texto_corregido = re.sub(error, correccion, texto_corregido, flags=re.IGNORECASE)
    return texto_corregido
        
