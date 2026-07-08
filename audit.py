import csv
from pathlib import Path

LISTO = Path(r"C:\Grok traduccion\listo")
RAW = Path(r"C:\Grok traduccion\Raw")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def key(row: dict[str, str]) -> tuple[str, str]:
    return row.get("section", row.get("\ufeffsection", "")), row["id"]


total = 0
for lf in sorted(LISTO.glob("*.csv")):
    rf = RAW / lf.name
    if not rf.exists():
        continue
    lr, rr = read_csv(lf), read_csv(rf)
    lg1 = [r for r in lr if r["group"] == "1"]
    rg1 = {key(r): r["text"] for r in rr if r["group"] == "1"}
    jg0 = {key(r): r["text"] for r in rr if r["group"] == "0"}
    for r in lg1:
        k = key(r)
        txt, en, jp = r["text"], rg1.get(k, ""), jg0.get(k, "")
        probs = []
        if ("<%me>" in en or "<%me>" in jp) and "<%me>" not in txt:
            probs.append("sin <%me>")
        if "⟦" in txt:
            probs.append("placeholder corrupto")
        if en.count("<br>") != txt.count("<br>"):
            probs.append(f"br {txt.count('<br>')}!={en.count('<br>')}")
        if probs:
            total += 1

print("filas con problemas en listo:", total)