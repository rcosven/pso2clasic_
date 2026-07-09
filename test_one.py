import csv
from pathlib import Path
from translate_batch import process_file, load_cache, save_cache

cache = load_cache()
n = process_file("un_010470_om.csv", cache)
save_cache(cache)
print("updated", n)
src = Path(r"C:\Grok traduccion\listo\un_010470_om.csv")
with src.open(encoding="utf-8", newline="") as f:
    rows = [r for r in csv.DictReader(f) if r["group"] == "1"]
for r in rows:
    print(r["id"], ":", r["text"])