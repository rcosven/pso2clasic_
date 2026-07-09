"""Corrige etiquetas traducidas por error (<amarillo> -> <yellow>, etc.)."""
import csv
import re
import shutil
from pathlib import Path

LISTO = Path(r"C:\Grok traduccion\listo")
RAW = Path(r"C:\Grok traduccion\Raw")
INPUT = Path(r"C:\Grok traduccion\archivos a traducir")

TAG_FIXES = {
    "<amarillo>": "<yellow>",
    "</amarillo>": "</yellow>",
    "<rojo>": "<red>",
    "</rojo>": "</red>",
    "<verde>": "<green>",
    "<azul>": "<blue>",
}

BAD_TAG_RE = re.compile(r"</?(?:amarillo|rojo|verde|azul)>", re.I)


def fix_text(text: str) -> str:
    for bad, good in TAG_FIXES.items():
        text = text.replace(bad, good)
    return text


def fix_file(path: Path) -> bool:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    changed = False
    for row in rows:
        if row.get("group", "").strip() != "1":
            continue
        old = row.get("text", "")
        new = fix_text(old)
        if new != old:
            row["text"] = new
            changed = True
    if changed:
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["section", "group", "id", "text"], lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(
                {
                    "section": r.get("section", r.get("\ufeffsection", "")),
                    "group": r["group"],
                    "id": r["id"],
                    "text": r["text"],
                }
                for r in rows
            )
    return changed


def requeue_for_retranslation(path: Path) -> None:
    """Mueve a archivos a traducir si existe referencia Raw para re-traducir."""
    raw = RAW / path.name
    if raw.exists():
        dest = INPUT / path.name
        if not dest.exists():
            shutil.copy2(path, dest)
        path.unlink()


def main() -> None:
    fixed = 0
    requeued = 0
    for path in LISTO.glob("*.csv"):
        content = path.read_text(encoding="utf-8-sig")
        if not BAD_TAG_RE.search(content):
            continue
        if fix_file(path):
            fixed += 1
            print(f"  etiquetas corregidas: {path.name}")
        requeue_for_retranslation(path)
        requeued += 1

    print(f"\nCorregidos: {fixed} | Re-encolados para retraducir: {requeued}")


if __name__ == "__main__":
    main()