import sqlite3
import os
import csv
import unicodedata
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

logger = logging.getLogger("discord.bot.db")

DEFAULT_DB_PATH = "data/traducciones.db"


def norm_search(s: str) -> str:
    """Normaliza texto para búsqueda (minúsculas, sin tildes)."""
    if not s:
        return ""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", s.lower())
        if not unicodedata.combining(c)
    )


def exact_line_key(t: str) -> str:
    if not t:
        return ""
    return norm_search(t.strip())


class DatabaseManager:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_dir()
        self.init_schema()

    def _ensure_dir(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL,
                line INTEGER NOT NULL,
                corpus TEXT NOT NULL,
                layer TEXT NOT NULL,
                stem TEXT NOT NULL,
                section TEXT NOT NULL,
                grp TEXT NOT NULL,
                row_id TEXT NOT NULL,
                text TEXT NOT NULL,
                text_norm TEXT NOT NULL,
                rare_chars INTEGER NOT NULL DEFAULT 0,
                corrupt_utf16 INTEGER NOT NULL DEFAULT 0,
                text_fixed TEXT NOT NULL DEFAULT '',
                is_new_line INTEGER NOT NULL DEFAULT 0
            )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_file ON translations(file)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_stem ON translations(stem)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_layer_corpus ON translations(layer, corpus)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_search ON translations(layer, corpus, is_new_line)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_sec_grp_id ON translations(file, section, grp, row_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_equal ON translations(layer, grp, stem, section, row_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tr_equal_corpus ON translations(layer, corpus, grp, stem, section, row_id)")
            conn.commit()

    def is_built(self) -> bool:
        if not Path(self.db_path).exists():
            return False
        try:
            with self.get_connection() as conn:
                count = conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
                return count > 1000
        except Exception:
            return False

    def get_total_count(self) -> int:
        try:
            with self.get_connection() as conn:
                res = conn.execute("SELECT COUNT(*) FROM translations WHERE layer = 'main'").fetchone()
                return res[0] if res else 0
        except Exception:
            return 0

    def build_database(self, bot_instance, force: bool = False) -> int:
        if not force and self.is_built():
            total = self.get_total_count()
            logger.info(f"Base de datos SQLite ya existente con {total} filas principales.")
            return total

        logger.info("Construyendo base de datos SQLite desde los CSVs...")
        bot_instance.cargar_lineas_nuevas()

        directorios_datos = ["Csv_Clasic", "Csv_Ngs", "Csv_Ngs_Raw", "Csv_Clasic_Raw"]
        
        # Eliminar tabla previa si es reconstrucción
        with self.get_connection() as conn:
            conn.execute("DROP TABLE IF EXISTS translations")
            conn.commit()
        self.init_schema()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("PRAGMA synchronous = OFF")
        c.execute("PRAGMA journal_mode = MEMORY")

        batch = []
        total_inserted = 0
        corrupt_count = 0
        new_flag_count = 0

        for dir_name in directorios_datos:
            ruta = Path(dir_name)
            if not ruta.exists():
                continue
            
            corpus = (
                "classic" if dir_name.startswith("Csv_Clasic")
                else "ng" if dir_name.startswith("Csv_Ngs")
                else "other"
            )
            layer = "raw" if "_raw" in dir_name.lower() else "main"

            for archivo_csv in ruta.glob("*.csv"):
                file_rel = f"{dir_name}/{archivo_csv.name}"
                stem = archivo_csv.name
                try:
                    with open(archivo_csv, "r", encoding="utf-8-sig") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            if "id" not in row:
                                continue
                            section = row.get("section", "") or ""
                            group = str(row.get("group", "") or "")
                            row_id = row.get("id", "") or ""
                            texto_original = row.get("text", "") or ""
                            texto_norm = norm_search(texto_original)

                            is_g1 = group == "1"
                            is_corrupt = (
                                bot_instance.is_utf16_swapped_corrupt(texto_original)
                                if is_g1
                                else False
                            )
                            is_rare = is_corrupt
                            text_fixed = (
                                bot_instance.fix_utf16_swapped(texto_original)
                                if is_corrupt
                                else ""
                            )
                            if is_rare:
                                corrupt_count += 1

                            is_new = False
                            if corpus in ("classic", "ng") and layer == "main":
                                is_new = (
                                    corpus,
                                    stem,
                                    section.strip(),
                                    group.strip(),
                                    row_id.strip(),
                                ) in bot_instance.new_line_keys
                                if is_new:
                                    new_flag_count += 1

                            batch.append((
                                file_rel,
                                reader.line_num,
                                corpus,
                                layer,
                                stem,
                                section,
                                group,
                                row_id,
                                texto_original,
                                texto_norm,
                                1 if is_rare else 0,
                                1 if is_corrupt else 0,
                                text_fixed,
                                1 if is_new else 0,
                            ))

                            if len(batch) >= 20000:
                                c.executemany("""
                                INSERT INTO translations (
                                    file, line, corpus, layer, stem, section, grp, row_id,
                                    text, text_norm, rare_chars, corrupt_utf16, text_fixed, is_new_line
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, batch)
                                total_inserted += len(batch)
                                batch = []
                except Exception as e:
                    logger.error(f"Error leyendo {archivo_csv.name} al indexar DB: {e}")

        if batch:
            c.executemany("""
            INSERT INTO translations (
                file, line, corpus, layer, stem, section, grp, row_id,
                text, text_norm, rare_chars, corrupt_utf16, text_fixed, is_new_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            total_inserted += len(batch)

        conn.commit()
        conn.close()

        logger.info(
            f"Base de datos SQLite generada con éxito. Filas totales: {total_inserted} "
            f"(corruptas group1: {corrupt_count}, nuevas: {new_flag_count})"
        )
        return total_inserted

    def count_file_translatable_lines(self, filepath: str) -> int:
        fpath = filepath.replace("\\", "/")
        try:
            with self.get_connection() as conn:
                res = conn.execute(
                    "SELECT COUNT(*) FROM translations WHERE file = ? AND grp = '1'",
                    (fpath,)
                ).fetchone()
                return res[0] if res else 0
        except Exception:
            return 0

    def _format_row(self, r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["group"] = d.get("grp", "")
        d["id"] = d.get("row_id", "")
        return d

    def get_file_rows(self, filepath: str) -> List[Dict[str, Any]]:
        fpath = filepath.replace("\\", "/")
        try:
            with self.get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM translations WHERE file = ? ORDER BY line ASC",
                    (fpath,)
                ).fetchall()
                return [self._format_row(r) for r in rows]
        except Exception as e:
            logger.error(f"Error consultando filas de archivo {fpath}: {e}")
            return []

    def get_file_comparison_rows(self, main_filepath: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        main_path = main_filepath.replace("\\", "/")
        raw_path = main_path.replace("Csv_Ngs", "Csv_Ngs_Raw").replace("Csv_Clasic", "Csv_Clasic_Raw")
        
        main_items = self.get_file_rows(main_path)
        raw_items = self.get_file_rows(raw_path)
        return main_items, raw_items

    def save_row(
        self,
        filename: str,
        orig_section: str,
        orig_group: str,
        orig_id: str,
        new_text: str,
        new_section: Optional[str] = None,
        new_group: Optional[str] = None,
        new_id: Optional[str] = None,
        is_corrupt: bool = False,
        text_fixed: str = "",
    ):
        fpath = filename.replace("\\", "/")
        sec = new_section if new_section is not None else orig_section
        grp = str(new_group if new_group is not None else orig_group)
        rid = new_id if new_id is not None else orig_id
        t_norm = norm_search(new_text)

        with self.get_connection() as conn:
            # Intentar actualizar
            c = conn.cursor()
            c.execute("""
            UPDATE translations
            SET text = ?, text_norm = ?, section = ?, grp = ?, row_id = ?,
                rare_chars = ?, corrupt_utf16 = ?, text_fixed = ?
            WHERE file = ? AND section = ? AND grp = ? AND row_id = ?
            """, (
                new_text, t_norm, sec, grp, rid,
                1 if is_corrupt else 0, 1 if is_corrupt else 0, text_fixed,
                fpath, orig_section, orig_group, orig_id
            ))
            if c.rowcount == 0:
                # Fila no existía, insertar
                stem = Path(fpath).name
                corpus = "classic" if fpath.startswith("Csv_Clasic") else "ng"
                layer = "raw" if "_raw" in fpath.lower() else "main"
                c.execute("""
                INSERT INTO translations (
                    file, line, corpus, layer, stem, section, grp, row_id,
                    text, text_norm, rare_chars, corrupt_utf16, text_fixed, is_new_line
                ) VALUES (?, 999999, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    fpath, corpus, layer, stem, sec, grp, rid,
                    new_text, t_norm, 1 if is_corrupt else 0, 1 if is_corrupt else 0, text_fixed
                ))
            conn.commit()

    def search_query(
        self,
        query: str,
        scope: str = "all",
        deep: bool = False,
        new_only: bool = False,
        file_only: bool = False,
        rare_only: bool = False,
        page: int = 1,
        per_page: int = 50,
        max_matches: int = 400,
        excluded_files_set: Optional[set] = None,
        excluded_lines_set: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Búsqueda principal rápida sobre SQLite."""
        if excluded_files_set is None:
            excluded_files_set = set()
        if excluded_lines_set is None:
            excluded_lines_set = set()

        query_norm = norm_search(query) if query else ""
        query_cmd = ",".join(p.strip() for p in query_norm.split(",")).rstrip(",") if query_norm else ""
        query_file = query_norm
        query_file_stem = query_file[:-4] if query_file.endswith(".csv") else query_file
        query_file_csv = query_file_stem + ".csv"

        sql_where = ["layer = 'main'"]
        params: List[Any] = []

        if scope == "classic":
            sql_where.append("corpus = 'classic'")
        elif scope == "ng":
            sql_where.append("corpus = 'ng'")

        if new_only:
            sql_where.append("is_new_line = 1")
            if query_norm:
                sql_where.append("(text_norm LIKE ? OR row_id LIKE ? OR section LIKE ? OR file LIKE ?)")
                like_p = f"%{query_norm}%"
                params.extend([like_p, like_p, like_p, like_p])
        elif file_only:
            sql_where.append("(stem LIKE ? OR file LIKE ?)")
            like_f = f"%{query_file_stem}%"
            params.extend([like_f, like_f])
        elif rare_only:
            sql_where.append("grp = '1' AND (rare_chars = 1 OR corrupt_utf16 = 1)")
            if query_norm:
                sql_where.append("text_norm LIKE ?")
                params.append(f"%{query_norm}%")
        else:
            if deep and query_norm:
                sql_where.append("(text_norm LIKE ? OR row_id LIKE ? OR section LIKE ?)")
                like_p = f"%{query_norm}%"
                params.extend([like_p, like_p, like_p])
            elif query_norm:
                sql_where.append("text_norm LIKE ?")
                params.append(f"%{query_norm}%")

        where_clause = " AND ".join(sql_where)
        sql = f"SELECT * FROM translations WHERE {where_clause} LIMIT {max_matches * 2}"

        coincidencias = []
        ids_vistos = set()

        with self.get_connection() as conn:
            cursor = conn.execute(sql, params)
            for row in cursor:
                fpath = row["file"]
                if fpath in excluded_files_set:
                    continue

                section = row["section"]
                grp = row["grp"]
                row_id = row["row_id"]
                line_key = f"{fpath}:{section}:{grp}:{row_id}"
                if line_key in excluded_lines_set:
                    continue

                clave_unica = f"{fpath}_{section}_{row_id}_{grp}"
                if clave_unica in ids_vistos:
                    continue
                ids_vistos.add(clave_unica)

                cmd = f"{section},{grp},{row_id}"
                match_where = "new" if new_only else ("file" if file_only else ("rare" if rare_only else "text"))

                entry = {
                    "file": fpath,
                    "id": row_id,
                    "section": section,
                    "group": grp,
                    "text": row["text"],
                    "cmd": cmd,
                    "match": match_where,
                    "corpus": row["corpus"],
                }
                if row["is_new_line"]:
                    entry["new"] = True
                    entry["nuevas"] = True
                if row["rare_chars"] or row["corrupt_utf16"]:
                    entry["rare"] = True
                    entry["corrupt"] = True
                    if row["text_fixed"]:
                        entry["text_fixed"] = row["text_fixed"]

                coincidencias.append(entry)
                if len(coincidencias) >= max_matches:
                    break

        total = len(coincidencias)
        total_pages = (total + per_page - 1) // per_page if total else 0
        if total_pages and page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        page_items = coincidencias[start : start + per_page]

        return {
            "items": page_items,
            "deep": deep,
            "new": new_only,
            "nuevas": new_only,
            "file": file_only,
            "byfile": file_only,
            "rare": rare_only,
            "corrupt": rare_only,
            "scope": scope,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "capped": total >= max_matches,
        }

    def search_equal_lines(
        self,
        seed_key: Optional[str] = None,
        equal_min_chars: int = 4,
        scope: str = "all",
        page: int = 1,
        per_page: int = 50,
        max_matches: int = 400,
        excluded_files_set: Optional[set] = None,
        excluded_lines_set: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Líneas iguales group 1 entre MAIN y RAW."""
        if excluded_files_set is None: excluded_files_set = set()
        if excluded_lines_set is None: excluded_lines_set = set()

        sql_scope = ""
        params: List[Any] = []
        if scope == "classic":
            sql_scope = "AND m.corpus = 'classic'"
        elif scope == "ng":
            sql_scope = "AND m.corpus = 'ng'"

        # Consulta directa que empareja MAIN y RAW con mismo stem, section, id y group=1
        sql = f"""
        SELECT m.file, m.stem, m.section, m.grp, m.row_id, m.text as main_text, r.text as raw_text, m.corpus
        FROM translations m
        JOIN translations r ON m.stem = r.stem AND m.corpus = r.corpus AND m.section = r.section AND m.row_id = r.row_id AND r.grp = '1' AND r.layer = 'raw'
        WHERE m.layer = 'main' AND m.grp = '1' {sql_scope}
        AND length(trim(m.text)) >= ?
        AND trim(m.text) = trim(r.text)
        """
        params.append(equal_min_chars)

        if seed_key:
            sql += " AND m.text_norm = ?"
            params.append(seed_key)

        sql += f" LIMIT {max_matches * 2}"

        coincidencias = []
        ids_vistos = set()
        with self.get_connection() as conn:
            for row in conn.execute(sql, params):
                fpath = row["file"]
                if fpath in excluded_files_set:
                    continue
                line_key = f"{fpath}:{row['section']}:{row['grp']}:{row['row_id']}"
                if line_key in excluded_lines_set:
                    continue

                clave_unica = f"{fpath}_{row['section']}_{row['row_id']}_{row['grp']}"
                if clave_unica in ids_vistos:
                    continue
                ids_vistos.add(clave_unica)

                cmd = f"{row['section']},{row['grp']},{row['row_id']}"
                coincidencias.append({
                    "file": fpath,
                    "id": row["row_id"],
                    "section": row["section"],
                    "group": row["grp"],
                    "text": row["main_text"],
                    "cmd": cmd,
                    "match": "equal",
                    "corpus": row["corpus"],
                    "layer": "main"
                })
                if len(coincidencias) >= max_matches:
                    break

        total = len(coincidencias)
        total_pages = (total + per_page - 1) // per_page if total else 0
        start = (page - 1) * per_page
        page_items = coincidencias[start : start + per_page]

        return {
            "items": page_items,
            "equal": True,
            "iguales": True,
            "chars": equal_min_chars,
            "min_chars": equal_min_chars,
            "scope": scope,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "capped": total >= max_matches,
        }

    def search_gap(
        self,
        diff_min_chars: int = 10,
        diff_compare: str = "both",
        scope: str = "all",
        query: str = "",
        page: int = 1,
        per_page: int = 50,
        max_matches: int = 400,
        excluded_files_set: Optional[set] = None,
        excluded_lines_set: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Diferencia de longitud entre texto RAW y MAIN."""
        if excluded_files_set is None: excluded_files_set = set()
        if excluded_lines_set is None: excluded_lines_set = set()

        sql_scope = ""
        params: List[Any] = []
        if scope == "classic":
            sql_scope = "AND m.corpus = 'classic'"
        elif scope == "ng":
            sql_scope = "AND m.corpus = 'ng'"

        sql = f"""
        SELECT m.file, m.stem, m.section, m.grp, m.row_id, m.text as main_text, r.text as raw_text, m.corpus
        FROM translations m
        JOIN translations r ON m.stem = r.stem AND m.corpus = r.corpus AND m.section = r.section AND m.row_id = r.row_id AND r.grp = '1' AND r.layer = 'raw'
        WHERE m.layer = 'main' AND m.grp = '1' {sql_scope}
        AND abs(length(trim(r.text)) - length(trim(m.text))) >= ?
        """
        params.append(diff_min_chars)

        if query:
            q_norm = norm_search(query)
            sql += " AND (m.text_norm LIKE ? OR m.row_id LIKE ? OR m.section LIKE ? OR m.stem LIKE ?)"
            like_q = f"%{q_norm}%"
            params.extend([like_q, like_q, like_q, like_q])

        sql += f" LIMIT {max_matches * 2}"

        coincidencias = []
        ids_vistos = set()
        with self.get_connection() as conn:
            for row in conn.execute(sql, params):
                fpath = row["file"]
                if fpath in excluded_files_set:
                    continue
                line_key = f"{fpath}:{row['section']}:{row['grp']}:{row['row_id']}"
                if line_key in excluded_lines_set:
                    continue

                clave_unica = f"{fpath}_{row['section']}_{row['row_id']}_{row['grp']}"
                if clave_unica in ids_vistos:
                    continue
                ids_vistos.add(clave_unica)

                raw_len = len(row["raw_text"].strip())
                main_len = len(row["main_text"].strip())
                char_diff = abs(raw_len - main_len)

                if diff_compare == "raw-shorter" and not (raw_len < main_len):
                    continue
                if diff_compare == "raw-longer" and not (raw_len > main_len):
                    continue

                cmd = f"{row['section']},{row['grp']},{row['row_id']}"
                coincidencias.append({
                    "file": fpath,
                    "id": row["row_id"],
                    "section": row["section"],
                    "group": row["grp"],
                    "text": row["main_text"],
                    "raw_text": row["raw_text"],
                    "cmd": cmd,
                    "match": "diff",
                    "corpus": row["corpus"],
                    "char_diff": char_diff,
                    "diff": char_diff,
                    "raw_len": raw_len,
                    "main_len": main_len,
                })
                if len(coincidencias) >= max_matches:
                    break

        coincidencias.sort(key=lambda x: x["char_diff"], reverse=True)
        total = len(coincidencias)
        total_pages = (total + per_page - 1) // per_page if total else 0
        start = (page - 1) * per_page
        page_items = coincidencias[start : start + per_page]

        return {
            "items": page_items,
            "diff": True,
            "brecha": True,
            "diff_chars": diff_min_chars,
            "min_diff": diff_min_chars,
            "diff_compare": diff_compare,
            "scope": scope,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "capped": total >= max_matches,
        }
