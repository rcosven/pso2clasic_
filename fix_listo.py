"""Re-traduce archivos en listo/ que perdieron <br> o <%me>."""
from pathlib import Path
from translate_batch import OUTPUT_DIR, load_cache, process_one_file, save_cache

LISTO = OUTPUT_DIR


def main() -> None:
    cache = load_cache()
    files = sorted(p.name for p in LISTO.glob("*.csv"))
    print(f"Reparando {len(files)} archivos en listo...", flush=True)
    for i, name in enumerate(files, 1):
        process_one_file(name, cache, input_dir=LISTO)
        print(f"   [{i}/{len(files)}] reparado", flush=True)
    save_cache(cache)
    print("Reparación completada.", flush=True)


if __name__ == "__main__":
    main()