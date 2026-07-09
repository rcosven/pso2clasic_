#!/usr/bin/env python3
"""
PSO2 CSV Spanish Translator - Railway + GitHub.
Usa Google Translate linea por linea (mismo metodo que translate_batch.py).
"""

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

from deep_translator import GoogleTranslator

# Subir este string cuando cambie la logica. Aparece en logs de Railway.
BOT_VERSION = "2026-07-09-v6-pull5min"

# Archivos del bot: NUNCA se pisan con git reset/pull (vienen del Docker/deploy).
CODE_FILES = (
    "translate_missing.py",
    "start.sh",
    "railway.toml",
    "Dockerfile",
    "requirements.txt",
    "nixpacks.toml",
    ".dockerignore",
    "config.py",
    "github_manager.py",
    "apply_translation.py",
    "audit.py",
    "fix_listo.py",
    "fix_tags.py",
    "test_one.py",
    "patch_ra_025020.py",
)

REPO_DIR = Path("/app")
INPUT_DIR = REPO_DIR / "archivos a traducir"
RAW_DIR = REPO_DIR / "Raw"
OUTPUT_DIR = REPO_DIR / "listo"
QUARANTINE_DIR = REPO_DIR / "Cuarentena"
CACHE_FILE = Path("/app/translation_cache.json")
LOG = Path("/app/translate_missing.log")
SCRIPT_PATH = Path(__file__).resolve()

GITHUB_REPO = os.getenv("GITHUB_REPO", "rcosven/pso2clasic_")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
TIEMPO_ENTRE_ARCHIVOS = int(os.getenv("TIEMPO_ENTRE_ARCHIVOS", "3"))
TIEMPO_SIN_PENDIENTES = int(os.getenv("TIEMPO_ESPERA", "600"))
TRADUCCION_TIMEOUT = int(os.getenv("TRADUCCION_TIMEOUT", "30"))
PUSH_CADA = int(os.getenv("PUSH_CADA", "5"))
# Minimo entre pulls (evita el ciclo eterno pull/push). Default 5 minutos.
MIN_PULL_INTERVAL = int(os.getenv("MIN_PULL_INTERVAL", "300"))
DEFER_FILES = {
    name.strip()
    for name in os.getenv("ARCHIVOS_DIFERIR", "apc_chat_3.csv").split(",")
    if name.strip()
}

# Timestamp del ultimo pull exitoso (epoch seconds).
_LAST_PULL_TS = 0.0


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


# Hash del codigo con el que arranco ESTE proceso (en memoria).
RUNNING_CODE_SHA = sha256_file(SCRIPT_PATH)

FIELDNAMES = ["section", "group", "id", "text"]
SKIP_IDS = {"name01", "name02"}
DATE_RE = re.compile(r"^A\.P\.?\s*\d")
PLACEHOLDER_QUESTION = re.compile(r"^[？?¿]+$")
BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>|<%[^%]+%>|\[[^\]]+\]|〔[^〕]+〕")

TAG_FIXES = {
    "<amarillo>": "<yellow>", "</amarillo>": "</yellow>",
    "<rojo>": "<red>", "</rojo>": "</red>",
    "<verde>": "<green>", "</verde>": "</green>",
    "<azul>": "<blue>", "</azul>": "</blue>",
    "<rosa>": "<pink>", "</rosa>": "</pink>",
    "<morado>": "<purple>", "</morado>": "</purple>",
}

translator = GoogleTranslator(source="en", target="es")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fix_broken_tags() -> None:
    fixed = 0
    for folder in (INPUT_DIR, OUTPUT_DIR):
        if not folder.exists():
            continue
        for csv_file in folder.rglob("*.csv"):
            try:
                content = csv_file.read_text(encoding="utf-8")
                new_content = content
                for bad, good in TAG_FIXES.items():
                    if bad in new_content:
                        new_content = new_content.replace(bad, good)
                        fixed += 1
                if new_content != content:
                    csv_file.write_text(new_content, encoding="utf-8")
            except Exception:
                pass
    if fixed:
        log(f"Se arreglaron {fixed} tags rotos.")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [
        {
            "section": row.get("section", row.get("\ufeffsection", "")),
            "group": row.get("group", ""),
            "id": row.get("id", ""),
            "text": row.get("text", ""),
        }
        for row in rows
    ]


def load_cache() -> dict[str, str]:
    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in cache.items() if "⟦" not in v and "PH000" not in v}
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def protect_tags(text: str) -> tuple[str, list[str]]:
    tags: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tags.append(match.group(0))
        return f"⟦{len(tags) - 1}⟧"

    return TAG_RE.sub(repl, text), tags


def restore_tags(text: str, tags: list[str]) -> str:
    for i, tag in enumerate(tags):
        text = text.replace(f"⟦{i}⟧", tag)
    return text


def is_already_spanish(text: str) -> bool:
    if not text or not text.strip():
        return True
    if re.search(r"[áéíóúñ¿¡]", text):
        return True
    sample = text[:80].lower()
    es_markers = ("tengo", "me ", "el ", "la ", "los ", "una ", "por ", "¿", "¡", "hasta", "voy ")
    en_markers = ("the ", "you ", "your ", "what ", "this ", "that ", "more ", "it's ", "ah,")
    has_es = any(m in sample for m in es_markers)
    has_en = any(m in sample for m in en_markers)
    return has_es and not has_en


def translate_segment(text: str, cache: dict[str, str], tags: list[str]) -> str:
    text = text.strip()
    if not text:
        return restore_tags(text, tags)
    if text in cache:
        return restore_tags(cache[text], tags)
    if is_already_spanish(text):
        cache[text] = text
        return restore_tags(text, tags)

    for attempt in range(5):
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(translator.translate, text)
                translated = future.result(timeout=TRADUCCION_TIMEOUT)
            result = (translated or "").strip()
            cache[text] = result
            time.sleep(0.1)
            return restore_tags(result, tags)
        except FuturesTimeout:
            log(f"Timeout traduccion ({attempt + 1}/5), reintentando...")
            time.sleep(2 ** attempt)
        except Exception as exc:
            log(f"Reintento traduccion ({attempt + 1}/5): {str(exc)[:120]}")
            time.sleep(2 ** attempt)
    raise RuntimeError(f"No se pudo traducir: {text[:60]!r}")


def translate_preserve_lines(en_text: str, jp_text: str, cache: dict[str, str]) -> str:
    parts = BR_RE.split(en_text)
    translated_parts = []
    for part in parts:
        protected, tags = protect_tags(part)
        translated_parts.append(translate_segment(protected, cache, tags))
    result = "<br>".join(translated_parts)

    jp_lead = ""
    if jp_text.startswith("<%me>"):
        if not en_text.startswith("<%me>") and not result.startswith("<%me>"):
            jp_lead = "<%me>"

    if jp_lead and result.startswith("<br>"):
        result = jp_lead + result
    elif jp_lead:
        result = jp_lead + ("<br>" if not result.startswith("<br>") else "") + result

    if ("<%me>" in en_text or "<%me>" in jp_text) and "<%me>" not in result:
        if jp_text.startswith("<%me><br>"):
            result = "<%me><br>" + result.lstrip("<br>")
        elif jp_text.startswith("<%me>"):
            result = "<%me>" + result

    return result


def normalize_date_from_jp(jp_text: str) -> str:
    match = re.match(r"^(A\.P\.\d+/.+)$", jp_text.strip())
    return match.group(1) if match else jp_text


def should_use_jp_text(row_id: str, jp_text: str, en_text: str) -> str | None:
    if row_id in SKIP_IDS:
        return "¿?"
    if PLACEHOLDER_QUESTION.match(jp_text.strip()) or PLACEHOLDER_QUESTION.match(en_text.strip()):
        return "¿?"
    if DATE_RE.match(jp_text.strip()) or DATE_RE.match(en_text.strip()):
        return normalize_date_from_jp(jp_text)
    return None


def backup_code_files() -> dict[str, bytes]:
    """Guarda el codigo del contenedor (Docker) para no perderlo con git reset."""
    out: dict[str, bytes] = {}
    for name in CODE_FILES:
        path = REPO_DIR / name
        if path.is_file():
            try:
                out[name] = path.read_bytes()
            except Exception:
                pass
    return out


def restore_code_files(backups: dict[str, bytes]) -> int:
    """Restaura el codigo del deploy tras sync con GitHub. Retorna cuantos archivos."""
    restored = 0
    for name, data in backups.items():
        path = REPO_DIR / name
        try:
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
                restored += 1
        except Exception as exc:
            log(f"No se pudo restaurar {name}: {exc}")
    return restored


def setup_git() -> bool:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log("ERROR CRITICO: Falta GITHUB_TOKEN en Railway.")
        return False

    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)

    remote_url = f"https://oauth2:{token}@github.com/{GITHUB_REPO}.git"
    fresh_init = not (REPO_DIR / ".git").exists()

    # Critico: el codigo del bot vive en la imagen Docker. git reset NO debe pisarlo.
    code_backup = backup_code_files()
    log(f"Codigo del deploy protegido: {len(code_backup)} archivos (v={BOT_VERSION})")

    if fresh_init:
        log("Inicializando Git en /app...")
        subprocess.run(["git", "init"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "branch", "-m", GITHUB_BRANCH], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)
    else:
        remotes = subprocess.run(["git", "remote"], cwd=REPO_DIR, capture_output=True, text=True)
        if "origin" in remotes.stdout.split():
            subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=REPO_DIR, check=False)
        else:
            subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)

    fetch = subprocess.run(
        ["git", "fetch", "origin", GITHUB_BRANCH],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        log(f"Error fetch GitHub: {fetch.stderr.strip() or fetch.stdout.strip()}")
        restore_code_files(code_backup)
        return False

    if fresh_init:
        sync = subprocess.run(
            ["git", "reset", "--hard", f"origin/{GITHUB_BRANCH}"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        if sync.returncode != 0:
            log(f"Error sync inicial: {sync.stderr.strip() or sync.stdout.strip()}")
            restore_code_files(code_backup)
            return False
    elif has_local_changes() or commits_ahead_of_remote() > 0:
        log("Hay cambios/commits locales sin subir, intentando push...")
        if not push_with_retry():
            log("Git listo con progreso local pendiente (NO se hara reset --hard).")
            restore_code_files(code_backup)
            return True
        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", GITHUB_BRANCH],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        if pull.returncode != 0:
            log(f"Aviso pull: {pull.stderr.strip() or pull.stdout.strip()}")
            subprocess.run(["git", "rebase", "--abort"], cwd=REPO_DIR, capture_output=True)
    else:
        # Solo reset de DATOS; el codigo se restaura abajo.
        sync = subprocess.run(
            ["git", "reset", "--hard", f"origin/{GITHUB_BRANCH}"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        if sync.returncode != 0:
            log(f"Error sync git: {sync.stderr.strip() or sync.stdout.strip()}")
            restore_code_files(code_backup)
            return False

    restored = restore_code_files(code_backup)
    if restored:
        log(f"Codigo del deploy restaurado tras git sync ({restored} archivos).")

    subprocess.run(
        ["git", "branch", "--set-upstream-to", f"origin/{GITHUB_BRANCH}", GITHUB_BRANCH],
        cwd=REPO_DIR,
        check=False,
    )

    # Limpieza inicial: basura vacia fuera de la cola.
    scrub_empty_from_queue()

    log("Conexion con GitHub establecida correctamente.")
    return True


def is_csv_empty(path: Path) -> bool:
    """True si no hay filas de datos (solo cabecera o archivo vacio)."""
    try:
        return len(read_csv(path)) == 0
    except Exception:
        return path.stat().st_size < 50


def force_delete(path: Path) -> None:
    """Borra un archivo si existe (varios intentos)."""
    for _ in range(3):
        try:
            if path.exists():
                path.unlink()
            return
        except Exception:
            time.sleep(0.05)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def quarantine_empty_file(path: Path) -> bool:
    """
    Aisla un CSV vacio:
    1) Copia a Cuarentena/ (si no estaba)
    2) SIEMPRE borra el original de la cola (archivos a traducir/)
    3) Borra copias accidentales en la raiz del proyecto

    El ciclo eterno era: copiar a Cuarentena y dejar el archivo en la cola.
    """
    if not path.exists():
        # Aun asi limpiar posibles restos con el mismo nombre en la cola
        leftover = INPUT_DIR / path.name
        if leftover.exists() and is_csv_empty(leftover):
            force_delete(leftover)
        return True

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    name = path.name
    dest = QUARANTINE_DIR / name
    try:
        # 1) Asegurar que existe en Cuarentena
        if not dest.exists():
            try:
                shutil.copy2(str(path), str(dest))
            except Exception:
                # si copy falla, intentar move como fallback
                if path.resolve() != dest.resolve():
                    shutil.move(str(path), str(dest))
        # 2) SIEMPRE borrar de la ruta de origen y de la cola
        force_delete(path)
        force_delete(INPUT_DIR / name)
        # 3) Borrar copias basura en la raiz /app (no Raw, no listo)
        root_copy = REPO_DIR / name
        if root_copy.is_file() and root_copy.resolve() != dest.resolve():
            try:
                if root_copy.stat().st_size < 50 or is_csv_empty(root_copy):
                    force_delete(root_copy)
            except Exception:
                pass
        still = (INPUT_DIR / name).exists()
        if still:
            log(f"ERROR: {name} sigue en cola tras aislar; reintento borrado.")
            force_delete(INPUT_DIR / name)
            still = (INPUT_DIR / name).exists()
        if still:
            log(f"CRITICO: no se pudo borrar {name} de 'archivos a traducir/'")
            return False
        log(f"AISLADO: {name} -> Cuarentena/ y BORRADO de cola")
        return True
    except Exception as exc:
        log(f"ERROR aislando {name}: {exc}")
        force_delete(path)
        force_delete(INPUT_DIR / name)
        return not (INPUT_DIR / name).exists()


def scrub_empty_from_queue() -> int:
    """
    Limpia de la cola TODOS los CSV vacios de una vez.
    Evita el bucle de miles de commits solo por gacha/banner vacios.
    Nunca reintroduce archivos que ya estan en Cuarentena.
    """
    ensure_input_dir()
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    cleaned = 0
    for path in list(INPUT_DIR.glob("*.csv")):
        if is_csv_empty(path):
            if quarantine_empty_file(path):
                cleaned += 1
    if cleaned:
        log(f"Limpieza cola: {cleaned} CSV vacios enviados a Cuarentena/borrados.")
    return cleaned


def drop_empty_cola_from_git_index() -> int:
    """
    Impide que vacios de 'archivos a traducir/' se suban o reaparezcan en el index.
    - Deshace stage de A/?? de CSV vacios
    - Borra el archivo del disco si sigue ahi
    """
    status = subprocess.run(
        ["git", "status", "--porcelain", "-u", "archivos a traducir"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    dropped = 0
    for raw in (status.stdout or "").splitlines():
        if not raw.strip():
            continue
        # Formato: XY PATH  o  XY "PATH CON ESPACIOS"
        path_part = raw[3:].strip()
        if path_part.startswith('"') and path_part.endswith('"'):
            path_part = path_part[1:-1]
        # renames: "old -> new"
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip().strip('"')
        if not path_part.replace("\\", "/").startswith("archivos a traducir/"):
            continue
        if not path_part.lower().endswith(".csv"):
            continue
        full = REPO_DIR / path_part
        xy = raw[:2]
        is_add = "A" in xy or xy == "??" or xy.strip() == "A"
        # Solo bloquear altas de vacios; los D (borrados) si se permiten.
        if not is_add:
            continue
        empty = (not full.exists()) or is_csv_empty(full)
        if not empty:
            continue
        # Sacar del index y del working tree
        subprocess.run(
            ["git", "rm", "-f", "--cached", "--ignore-unmatch", path_part],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        if full.exists():
            quarantine_empty_file(full)
        dropped += 1
    if dropped:
        log(f"Bloqueados {dropped} CSV vacios: no se suben ni se re-anaden a la cola.")
    return dropped


def commits_ahead_of_remote() -> int:
    """Cuantos commits locales no estan en origin/branch."""
    subprocess.run(
        ["git", "fetch", "origin", GITHUB_BRANCH],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    res = subprocess.run(
        ["git", "rev-list", "--count", f"origin/{GITHUB_BRANCH}..HEAD"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return 0
    try:
        return int((res.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def push_existing_commits() -> bool:
    """Intenta push de commits ya hechos. Si remote avanzo, fetch+rebase y reintenta."""
    push = subprocess.run(
        ["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if push.returncode == 0:
        log("Push completado en GitHub.")
        return True

    err = (push.stderr or push.stdout or "").strip()
    log(f"Error en push: {err[:300]}")

    if "rejected" not in err.lower() and "fetch first" not in err.lower():
        return False

    log("Remote adelantado: fetch + rebase y reintento de push...")
    fetch = subprocess.run(
        ["git", "fetch", "origin", GITHUB_BRANCH],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        log(f"Error fetch: {(fetch.stderr or fetch.stdout or '')[:200]}")
        return False

    rebase = subprocess.run(
        ["git", "rebase", f"origin/{GITHUB_BRANCH}"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if rebase.returncode != 0:
        log(f"Error rebase: {(rebase.stderr or rebase.stdout or '')[:300]}")
        subprocess.run(["git", "rebase", "--abort"], cwd=REPO_DIR, capture_output=True)
        return False

    push2 = subprocess.run(
        ["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if push2.returncode != 0:
        log(f"Error en push tras rebase: {(push2.stderr or push2.stdout or '')[:300]}")
        return False

    log("Push completado en GitHub (tras rebase).")
    return True


def push_with_retry(max_attempts: int = 3) -> bool:
    for intento in range(1, max_attempts + 1):
        if push_to_github():
            return True
        if intento < max_attempts:
            log(f"Reintentando push ({intento}/{max_attempts})...")
            time.sleep(5)
    log("CRITICO: No se pudo subir a GitHub tras varios intentos.")
    return False


def sync_github() -> bool:
    """
    PUSH local, luego PULL de datos (minimo MIN_PULL_INTERVAL segundos entre pulls).
    Tras el pull se purgan CSV vacios y se borran de la cola.
    """
    global _LAST_PULL_TS

    scrub_empty_from_queue()
    drop_empty_cola_from_git_index()
    code_backup = backup_code_files()

    # Siempre intentar guardar progreso local (push), aunque no hagamos pull.
    if has_local_changes() or commits_ahead_of_remote() > 0:
        log("Subiendo cambios locales a GitHub...")
        if not push_with_retry():
            log("CRITICO: no se pudo push; se omite pull para no perder progreso.")
            restore_code_files(code_backup)
            return False

    now = time.time()
    elapsed = now - _LAST_PULL_TS if _LAST_PULL_TS > 0 else MIN_PULL_INTERVAL
    if _LAST_PULL_TS > 0 and elapsed < MIN_PULL_INTERVAL:
        wait_left = int(MIN_PULL_INTERVAL - elapsed)
        log(f"Pull omitido: minimo {MIN_PULL_INTERVAL}s entre pulls (faltan ~{wait_left}s).")
        restore_code_files(code_backup)
        return True

    log(f"Pull desde GitHub (intervalo min={MIN_PULL_INTERVAL}s)...")
    pull = subprocess.run(
        ["git", "pull", "--rebase", "origin", GITHUB_BRANCH],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if pull.returncode != 0:
        log(f"Error en pull --rebase: {pull.stderr.strip() or pull.stdout.strip()}")
        subprocess.run(["git", "rebase", "--abort"], cwd=REPO_DIR, capture_output=True)
        pull = subprocess.run(
            ["git", "pull", "--ff-only", "origin", GITHUB_BRANCH],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        if pull.returncode != 0:
            log(f"Error en pull: {pull.stderr.strip() or pull.stdout.strip()}")
            restore_code_files(code_backup)
            return False

    _LAST_PULL_TS = time.time()

    restored = restore_code_files(code_backup)
    if restored:
        log(f"Codigo del deploy preservado tras pull ({restored} archivos).")

    # Critico: el pull puede reintroducir vacios. Aislar + borrar de cola.
    cleaned = scrub_empty_from_queue()
    drop_empty_cola_from_git_index()
    if cleaned:
        log(f"Post-pull: {cleaned} vacios descartados (borrados de cola, en Cuarentena).")

    out = pull.stdout or ""
    if "Already up to date" in out or "up to date" in out.lower():
        log("Pull: repositorio actualizado.")
    else:
        log("Pull completado: repo sincronizado con GitHub.")
    return True


def has_local_changes() -> bool:
    # Solo cambios reales: no contar basura vacia sin stagear.
    scrub_empty_from_queue()
    drop_empty_cola_from_git_index()
    status = subprocess.run(
        ["git", "status", "--porcelain", "listo", "archivos a traducir", "Cuarentena", "translation_cache.json"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    return bool(status.stdout.strip())


def push_to_github() -> bool:
    ensure_input_dir()
    # 1) Quitar vacios del disco
    scrub_empty_from_queue()
    # 2) NUNCA stagear altas de vacios en la cola
    drop_empty_cola_from_git_index()

    # listo/: traducciones nuevas (si se permiten altas)
    subprocess.run(
        ["git", "-c", "diff.renames=false", "add", "-A", "listo"],
        cwd=REPO_DIR,
        check=False,
    )
    # cola/: SOLO -u (update) = borrados/modificaciones de tracked.
    # NUNCA -A: eso re-subia gacha vacios como "A archivos a traducir/..."
    subprocess.run(
        ["git", "-c", "diff.renames=false", "add", "-u", "archivos a traducir"],
        cwd=REPO_DIR,
        check=False,
    )
    # Cuarentena: si, altas de vacios aislados
    subprocess.run(
        ["git", "-c", "diff.renames=false", "add", "-A", "Cuarentena"],
        cwd=REPO_DIR,
        check=False,
    )
    if CACHE_FILE.exists():
        subprocess.run(["git", "add", "-f", "translation_cache.json"], cwd=REPO_DIR, check=False)

    # Cinturon de seguridad: si algo vacio se stageo igual, sacarlo
    drop_empty_cola_from_git_index()

    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    lines = [ln for ln in (status.stdout or "").strip().split("\n") if ln.strip()]
    # Ignorar lineas que sean solo basura de cola vacia
    useful = []
    for ln in lines:
        if 'archivos a traducir/' in ln and ("A " in ln[:3] or ln.startswith("??") or ln.startswith("A ")):
            # no deberia quedar, pero por si acaso
            continue
        useful.append(ln)

    ahead = commits_ahead_of_remote()

    if not useful and ahead <= 0:
        log("Push omitido: nada util que subir (sin vacios, sin traducciones).")
        return True

    if useful:
        log("Guardando cambios en GitHub (Push)...")
        log(f"Cambios a subir: {len(useful)}")
        for ln in useful[:5]:
            log(f"  {ln[:140]}")

        commit = subprocess.run(
            ["git", "commit", "-m", "Bot: Traducciones Google Translate"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            msg = (commit.stderr or commit.stdout or "").strip()
            if "nothing to commit" in msg.lower():
                log("Nada nuevo que commitear; intentando push de commits pendientes...")
            else:
                log(f"Error en commit: {msg[:300]}")
                if ahead <= 0:
                    return False
    elif ahead > 0:
        log(f"Hay {ahead} commit(s) local(es) sin push; reintentando subida...")

    return push_existing_commits()


def process_one_file(filename: str, cache: dict[str, str]) -> int:
    input_path = INPUT_DIR / filename
    output_path = OUTPUT_DIR / filename
    raw_path = RAW_DIR / filename

    if not input_path.exists():
        return 0

    rows = read_csv(input_path)
    if not rows:
        return 1 if quarantine_empty_file(input_path) else 0

    # Si el usuario puso el archivo en "archivos a traducir/", hay que procesarlo.
    if output_path.exists():
        log(f"RE-TRADUCIENDO: {filename} (ya existe en listo/, se sobrescribira)")

    raw_rows = read_csv(raw_path)
    raw_g1 = {(r["section"], r["id"]): r["text"] for r in raw_rows if r["group"] == "1"}
    jp_g0 = {(r["section"], r["id"]): r["text"] for r in raw_rows if r["group"] == "0"}

    g1_rows = [r for r in rows if r["group"] == "1"]
    if not g1_rows:
        log(f"Sin group=1 en {filename}: se copia a listo/ sin traducir.")
    else:
        log(f"Traduciendo: {filename} ({len(g1_rows)} filas group=1)")
    progress_step = 1 if len(g1_rows) <= 25 else 5

    for i, row in enumerate(g1_rows, 1):
        key = (row["section"], row["id"])
        jp_text = jp_g0.get(key, "")
        # Preferir ingles de Raw si existe; si no, el texto del archivo en cola.
        en_text = raw_g1.get(key, row["text"])
        override = should_use_jp_text(row["id"], jp_text, en_text)
        if override is not None:
            row["text"] = override
        elif is_already_spanish(row["text"]) and is_already_spanish(en_text):
            pass
        else:
            log(f"  fila {i}/{len(g1_rows)}: {row['id']}")
            row["text"] = translate_preserve_lines(en_text, jp_text, cache)
        if i % progress_step == 0 or i == len(g1_rows):
            save_cache(cache)
            log(f"  ... {i}/{len(g1_rows)} filas")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    # Solo borrar de la cola DESPUES de guardar bien en listo/
    if output_path.exists() and output_path.stat().st_size > 0:
        force_delete(input_path)
        force_delete(INPUT_DIR / filename)
        if input_path.exists():
            log(f"ERROR: {filename} sigue en cola tras traducir.")
            return 0
    else:
        log(f"ERROR: no se borro {filename} de la cola porque listo/ quedo vacio o no se creo.")
        return 0

    save_cache(cache)
    log(f"OK -> listo/{filename} ({len(g1_rows)} filas group=1) | cola borrada")
    return 1


def pick_next_file(pending: list[Path]) -> Path | None:
    """
    Elige el siguiente CSV a traducir.
    - Ignora vacios (se limpian aparte).
    - Prefiere archivos con contenido real (no solo livianos vacios).
    - DEFER_FILES al final.
    """
    if not pending:
        return None

    usable = [p for p in pending if not is_csv_empty(p)]
    if not usable:
        return None

    normal = [p for p in usable if p.name not in DEFER_FILES]
    deferred = [p for p in usable if p.name in DEFER_FILES]
    pool = normal if normal else deferred
    # Livianos con contenido primero (traducen rapido y desbloquean la cola).
    return min(pool, key=lambda p: p.stat().st_size)


def ensure_input_dir() -> None:
    """Mantiene la carpeta de cola aunque este vacia (Git no versiona dirs vacios)."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    gitkeep = INPUT_DIR / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")


def process_next_file(cache: dict[str, str]) -> int:
    """Traduce un solo archivo. Retorna 1 si hubo cambio, 0 si no hay pendientes."""
    ensure_input_dir()
    OUTPUT_DIR.mkdir(exist_ok=True)
    QUARANTINE_DIR.mkdir(exist_ok=True)

    # Primero sacar basura vacia de la cola (un solo lote, no 1 commit por archivo).
    cleaned = scrub_empty_from_queue()
    if cleaned:
        return cleaned  # que se haga un solo push con todos los vacios

    pending = list(INPUT_DIR.glob("*.csv"))
    if not pending:
        log("No hay archivos pendientes en 'archivos a traducir/'.")
        return 0

    next_file = pick_next_file(pending)
    if not next_file:
        log("Solo habia CSV vacios; cola limpia de basura.")
        return 0

    filename = next_file.name
    size_kb = next_file.stat().st_size / 1024
    restantes = len(pending)
    defer_note = " (diferido al final)" if filename in DEFER_FILES else ""
    log(f"Pendientes: {restantes} | Procesando: {filename} ({size_kb:.1f} KB){defer_note}")
    try:
        return process_one_file(filename, cache)
    except Exception as exc:
        # NUNCA borrar el pendiente si fallo: debe reintentarse en el siguiente ciclo.
        save_cache(cache)
        log(f"ERROR en {filename}: {exc} (archivo se conserva en cola para reintento)")
        return 0


def restart_if_script_updated(reason: str = "git pull") -> None:
    """
    Si git actualizo translate_missing.py en disco pero este proceso sigue con
    el codigo viejo en memoria, reinicia el proceso para cargar la version nueva.
    """
    disk_sha = sha256_file(SCRIPT_PATH)
    if not disk_sha or disk_sha == RUNNING_CODE_SHA:
        return
    log(f"Codigo nuevo en disco tras {reason}. Reiniciando bot (v={BOT_VERSION})...")
    log(f"  sha memoria={RUNNING_CODE_SHA[:12]}... disco={disk_sha[:12]}...")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, str(SCRIPT_PATH), *sys.argv[1:]])


def main() -> None:
    global _LAST_PULL_TS
    LOG.write_text("", encoding="utf-8")
    log("=== Traductor PSO2 ES (Google Translate + Railway + GitHub) ===")
    log(f"BOT_VERSION={BOT_VERSION}")
    log(f"script_sha={RUNNING_CODE_SHA[:16]}...")
    log(
        f"Modo: 1 archivo/ciclo | Push cada {PUSH_CADA} | "
        f"Pull min {MIN_PULL_INTERVAL}s | Entre archivos {TIEMPO_ENTRE_ARCHIVOS}s"
    )
    if DEFER_FILES:
        log(f"Archivos diferidos al final: {', '.join(sorted(DEFER_FILES))}")

    git_ready = setup_git()
    _LAST_PULL_TS = time.time()  # setup_git ya hizo fetch/sync inicial
    restart_if_script_updated("setup_git")

    cache = load_cache()
    modificaciones = 0

    while True:
        if not git_ready:
            log("Reintentando conexion con GitHub...")
            git_ready = setup_git()
            _LAST_PULL_TS = time.time()
            restart_if_script_updated("setup_git retry")

        if git_ready:
            sync_github()
            restart_if_script_updated("sync_github")
        else:
            log("CRITICO: GitHub no disponible. El progreso NO se guardara en la nube.")

        # No escanear todo listo/ cada ciclo (era lento); solo cola.
        cambios = process_next_file(cache)
        if cambios:
            modificaciones += cambios
            log(f"Modificaciones acumuladas: {modificaciones}/{PUSH_CADA}")
            if not git_ready:
                log("CRITICO: archivo traducido pero Git no configurado.")
            elif modificaciones >= PUSH_CADA:
                if push_with_retry():
                    modificaciones = 0
                else:
                    git_ready = False
        elif git_ready and modificaciones > 0:
            log(f"Sin pendientes de lote. Guardando {modificaciones} cambios...")
            if push_with_retry():
                modificaciones = 0
            else:
                git_ready = False

        pendientes_restantes = len(list(INPUT_DIR.glob("*.csv")))
        if pendientes_restantes > 0:
            log(f"Siguiente archivo en {TIEMPO_ENTRE_ARCHIVOS}s ({pendientes_restantes} pendientes)...")
            time.sleep(TIEMPO_ENTRE_ARCHIVOS)
        else:
            log(f"Sin pendientes. Esperando {TIEMPO_SIN_PENDIENTES / 60} minutos...")
            time.sleep(TIEMPO_SIN_PENDIENTES)


if __name__ == "__main__":
    main() 
