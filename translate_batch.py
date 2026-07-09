"""Traduce un CSV, lo guarda en listo/ y pasa al siguiente."""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

from deep_translator import GoogleTranslator

INPUT_DIR = Path(r"C:\Grok traduccion\archivos a traducir")
RAW_DIR = Path(r"C:\Grok traduccion\Raw")
OUTPUT_DIR = Path(r"C:\Grok traduccion\listo")
CACHE_FILE = Path(r"C:\Grok traduccion\translation_cache.json")

translator = GoogleTranslator(source="en", target="es")
FIELDNAMES = ["section", "group", "id", "text"]

SKIP_IDS = {"name01", "name02"}
DATE_RE = re.compile(r"^A\.P\.?\s*\d")
PLACEHOLDER_QUESTION = re.compile(r"^[？?¿]+$")
BR_RE = re.compile(r"<br\s*/?>", re.I)


def read_csv(path: Path) -> list[dict[str, str]]:
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


TAG_RE = re.compile(r"<[^>]+>|<%[^%]+%>|\[[^\]]+\]|〔[^〕]+〕")


def protect_tags(text: str) -> tuple[str, list[str]]:
    """Reserva etiquetas/comandos (<yellow>, <c>, <%me>, etc.) sin traducir."""
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
            time.sleep(0.1)
            return restore_tags(result, tags)
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"No se pudo traducir: {text[:60]!r}")


def translate_preserve_lines(en_text: str, jp_text: str, cache: dict[str, str]) -> str:
    """Traduce por segmentos separados por <br> sin tocar etiquetas/comandos."""
    parts = BR_RE.split(en_text)
    translated_parts = []
    for part in parts:
        protected, tags = protect_tags(part)
        translated_parts.append(translate_segment(protected, cache, tags))
    result = "<br>".join(translated_parts)

    # Si el japonés tiene <%me> al inicio y la referencia EN no, conservarlo
    jp_lead = ""
    if jp_text.startswith("<%me>"):
        if en_text.startswith("<%me>"):
            pass
        elif en_text.startswith("<br>") or not en_text.startswith("<%me>"):
            if not result.startswith("<%me>"):
                jp_lead = "<%me>"

    if jp_lead and result.startswith("<br>"):
        result = jp_lead + result
    elif jp_lead:
        result = jp_lead + ("<br>" if not result.startswith("<br>") else "") + result

    # Asegurar <%me> si estaba en EN o JP
    if ("<%me>" in en_text or "<%me>" in jp_text) and "<%me>" not in result:
        if jp_text.startswith("<%me><br>"):
            result = "<%me><br>" + result.lstrip("<br>")
        elif jp_text.startswith("<%me>"):
            result = "<%me>" + result

    return result


def normalize_date_from_jp(jp_text: str) -> str:
    m = re.match(r"^(A\.P\.\d+/.+)$", jp_text.strip())
    return m.group(1) if m else jp_text


def should_use_jp_text(row_id: str, jp_text: str, en_text: str) -> str | None:
    if row_id in SKIP_IDS:
        return "¿?"
    if PLACEHOLDER_QUESTION.match(jp_text.strip()) or PLACEHOLDER_QUESTION.match(en_text.strip()):
        return "¿?"
    if DATE_RE.match(jp_text.strip()) or DATE_RE.match(en_text.strip()):
        return normalize_date_from_jp(jp_text)
    return None


def process_one_file(filename: str, cache: dict[str, str], input_dir: Path = INPUT_DIR) -> int:
    input_path = input_dir / filename
    output_path = OUTPUT_DIR / filename

    if not input_path.exists():
        return 0

    print(f"\n>> Traduciendo: {filename}", flush=True)

    rows = read_csv(input_path)
    raw_rows = read_csv(RAW_DIR / filename)
    raw_g1 = {(r["section"], r["id"]): r["text"] for r in raw_rows if r["group"] == "1"}
    jp_g0 = {(r["section"], r["id"]): r["text"] for r in raw_rows if r["group"] == "0"}

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
            print(f"   ... {i}/{len(g1_rows)} filas", flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    if input_dir == INPUT_DIR:
        input_path.unlink(missing_ok=True)
    save_cache(cache)
    print(f"   OK -> listo\\{filename} ({len(g1_rows)} filas group=1)", flush=True)
    return len(g1_rows)


def main() -> None:
    cache = load_cache()
    total_rows = 0
    done = 0

    while True:
        pending = sorted(INPUT_DIR.glob("*.csv"))
        if not pending:
            break
        name = pending[0].name
        total = len(pending)
        print(f"\nPendientes: {total}", flush=True)
        try:
            count = process_one_file(name, cache)
            total_rows += count
            done += 1
            print(f"   Progreso: {done} completados, {total - 1} restantes", flush=True)
        except Exception as exc:  # noqa: BLE001
            save_cache(cache)
            print(f"   ERROR en {name}: {exc}", flush=True)
            out = OUTPUT_DIR / name
            inp = INPUT_DIR / name
            if out.exists():
                inp.unlink(missing_ok=True)
            else:
                raise

    print(f"\nCompletado: {total_rows} filas en {done} archivos.", flush=True)


if __name__ == "__main__":
    main()