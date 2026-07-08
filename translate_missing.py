#!/usr/bin/env python3
"""
PSO2 CSV Spanish Translator - Railway + GitHub.
Usa Google Translate linea por linea (mismo metodo que translate_batch.py).
"""

import csv
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from deep_translator import GoogleTranslator

REPO_DIR = Path("/app")
INPUT_DIR = REPO_DIR / "archivos a traducir"
RAW_DIR = REPO_DIR / "Raw"
OUTPUT_DIR = REPO_DIR / "listo"
QUARANTINE_DIR = REPO_DIR / "Cuarentena"
CACHE_FILE = Path("/app/translation_cache.json")
LOG = Path("/app/translate_missing.log")

GITHUB_REPO = os.getenv("GITHUB_REPO", "rcosven/pso2clasic_")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
TIEMPO_ESPERA = int(os.getenv("TIEMPO_ESPERA", "600"))
PUSH_CADA = int(os.getenv("PUSH_CADA", "1"))

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


def translate_segment(text: str, cache: dict[str, str], tags: list[str]) -> str:
    text = text.strip()
    if not text:
        return restore_tags(text, tags)
    if text in cache:
        return restore_tags(cache[text], tags)

    for attempt in range(5):
        try:
            translated = translator.translate(text)
            result = (translated or "").strip()
            cache[text] = result
            time.sleep(0.15)
            return restore_tags(result, tags)
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


def setup_git() -> bool:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log("ERROR CRITICO: Configura GITHUB_TOKEN en Railway.")
        return False

    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)

    remote_url = f"https://oauth2:{token}@github.com/{GITHUB_REPO}.git"

    if not (REPO_DIR / ".git").exists():
        log("Inicializando Git en /app (contenedor sin historial)...")
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
        log(f"Error al conectar con GitHub: {fetch.stderr.strip() or fetch.stdout.strip()}")
        return False

    if has_local_changes():
        log("Guardando trabajo local antes de sincronizar rama...")
        push_with_retry()

    checkout = subprocess.run(
        ["git", "checkout", "-B", GITHUB_BRANCH, f"origin/{GITHUB_BRANCH}"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if checkout.returncode != 0:
        log(f"Error sincronizando rama: {checkout.stderr.strip() or checkout.stdout.strip()}")
        return False

    log("Conexion con GitHub establecida correctamente.")
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
    """Primero PUSH (guardar), luego PULL (traer nuevos). Nunca pull si push fallo."""
    if has_local_changes():
        log("Subiendo cambios locales a GitHub antes del pull...")
        if not push_with_retry():
            log("CRITICO: Pull cancelado para no perder progreso local.")
            return False

    pull = subprocess.run(
        ["git", "pull", "--ff-only", "origin", GITHUB_BRANCH],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if pull.returncode != 0:
        log(f"Error en pull: {pull.stderr.strip() or pull.stdout.strip()}")
        return False

    if "Already up to date" in pull.stdout:
        log("Pull: repositorio actualizado.")
    else:
        log("Pull completado: nuevos archivos descargados de GitHub.")
    return True


def has_local_changes() -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain", "listo", "archivos a traducir", "Cuarentena", "translation_cache.json"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    return bool(status.stdout.strip())


def push_to_github() -> bool:
    subprocess.run(["git", "add", "-A", "listo"], cwd=REPO_DIR, check=False)
    subprocess.run(["git", "add", "-A", "archivos a traducir"], cwd=REPO_DIR, check=False)
    subprocess.run(["git", "add", "-A", "Cuarentena"], cwd=REPO_DIR, check=False)
    if CACHE_FILE.exists():
        subprocess.run(["git", "add", "-f", "translation_cache.json"], cwd=REPO_DIR, check=False)

    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip():
        log("Push omitido: Git no detecta cambios pendientes.")
        return False

    log("Guardando cambios en GitHub (Push)...")
    log(status.stdout.strip().split("\n")[0][:120])

    commit = subprocess.run(
        ["git", "commit", "-m", "Bot: Traducciones Google Translate"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        log(f"Error en commit: {commit.stderr.strip() or commit.stdout.strip()}")
        return False

    push = subprocess.run(
        ["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        log(f"Error en push: {push.stderr.strip() or push.stdout.strip()}")
        return False

    log("Push completado en GitHub.")
    return True


def process_one_file(filename: str, cache: dict[str, str]) -> int:
    input_path = INPUT_DIR / filename
    output_path = OUTPUT_DIR / filename
    raw_path = RAW_DIR / filename

    if not input_path.exists():
        return 0

    if output_path.exists():
        log(f"OMITIDO: {filename} ya traducido en listo/")
        input_path.unlink(missing_ok=True)
        return 1

    rows = read_csv(input_path)
    if not rows:
        log(f"AISLADO: {filename} vacio, movido a Cuarentena.")
        QUARANTINE_DIR.mkdir(exist_ok=True)
        shutil.move(str(input_path), str(QUARANTINE_DIR / filename))
        return 1

    raw_rows = read_csv(raw_path)
    raw_g1 = {(r["section"], r["id"]): r["text"] for r in raw_rows if r["group"] == "1"}
    jp_g0 = {(r["section"], r["id"]): r["text"] for r in raw_rows if r["group"] == "0"}

    log(f"Traduciendo: {filename}")
    g1_rows = [r for r in rows if r["group"] == "1"]

    for i, row in enumerate(g1_rows, 1):
        key = (row["section"], row["id"])
        jp_text = jp_g0.get(key, "")
        en_text = raw_g1.get(key, row["text"])
        override = should_use_jp_text(row["id"], jp_text, en_text)
        if override is not None:
            row["text"] = override
        else:
            row["text"] = translate_preserve_lines(en_text, jp_text, cache)
        if i % 10 == 0:
            save_cache(cache)
            log(f"  ... {i}/{len(g1_rows)} filas")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    input_path.unlink(missing_ok=True)
    save_cache(cache)
    log(f"OK -> listo/{filename} ({len(g1_rows)} filas group=1)")
    return 1


def process_next_file(cache: dict[str, str]) -> int:
    """Traduce un solo archivo. Retorna 1 si hubo cambio, 0 si no hay pendientes."""
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    QUARANTINE_DIR.mkdir(exist_ok=True)

    pending = sorted(INPUT_DIR.glob("*.csv"))
    if not pending:
        log("No hay archivos pendientes en 'archivos a traducir/'.")
        return 0

    filename = pending[0].name
    restantes = len(pending)
    log(f"Pendientes: {restantes} | Procesando: {filename}")
    try:
        return process_one_file(filename, cache)
    except Exception as exc:
        save_cache(cache)
        log(f"ERROR en {filename}: {exc}")
        if (OUTPUT_DIR / filename).exists():
            (INPUT_DIR / filename).unlink(missing_ok=True)
        return 0


def main() -> None:
    LOG.write_text("", encoding="utf-8")
    log("=== Traductor PSO2 ES (Google Translate + Railway + GitHub) ===")
    log(f"Modo: 1 archivo por ciclo | Push cada {PUSH_CADA} modificaciones")

    git_ready = setup_git()
    if git_ready:
        log("GitHub: listo para pull/push.")
        sync_github()
    else:
        log("CRITICO: GitHub no disponible. El progreso NO se guardara en la nube.")

    cache = load_cache()
    modificaciones = 0

    while True:
        if git_ready:
            sync_github()
        fix_broken_tags()

        cambios = process_next_file(cache)
        if cambios:
            modificaciones += cambios
            log(f"Modificaciones acumuladas: {modificaciones}/{PUSH_CADA}")
            if not git_ready:
                log("CRITICO: archivo traducido pero Git no configurado.")
            elif modificaciones >= PUSH_CADA:
                if push_with_retry():
                    modificaciones = 0
        elif git_ready and modificaciones > 0:
            log(f"Sin pendientes. Guardando {modificaciones} cambios restantes...")
            if push_with_retry():
                modificaciones = 0

        log(f"Esperando {TIEMPO_ESPERA / 60} minutos...")
        time.sleep(TIEMPO_ESPERA)


if __name__ == "__main__":
    main()