# === NUEVO MOTOR DE TRADUCCIÓN CON CONTEXTO (GRUPO 0) ===
def batch_translate(conn, text_data: list[tuple[str, str]], src_lang: str) -> dict[str, str]:
    out_map: dict[str, str] = {}
    pending: list[tuple[str, str]] = []
    
    # text_data ahora es una lista de tuplas: (texto_a_traducir, contexto_japones)
    for text, context in text_data:
        if should_skip_translate(text):
            out_map[text] = text
            continue
        row = conn.execute("SELECT dst FROM cache WHERE src=? AND lang=?", (text, src_lang)).fetchone()
        if row: 
            out_map[text] = row[0]
        else: 
            pending.append((text, context))
        
    if not pending: return out_map
    
    # Reducimos el lote a 15 porque ahora enviamos el doble de texto (inglés + japonés)
    chunk_size = 15
    
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i:i + chunk_size]
        
        # Preparamos un JSON enriquecido para la IA
        chunk_dict = {}
        for idx, (txt, ctx) in enumerate(chunk):
            chunk_dict[str(idx)] = {
                "contexto_original": ctx,
                "texto_a_traducir": txt
            }
        
        prompt = f"""Eres un traductor profesional de videojuegos trabajando en Phantasy Star Online 2 (PSO2).
Recibirás un objeto JSON con los textos a procesar. Cada elemento contiene:
- 'contexto_original': El texto en japonés original. NO LO TRADUZCAS, úsalo únicamente para entender el contexto, género, tono y evitar malas interpretaciones.
- 'texto_a_traducir': El texto que DEBES traducir al español.

REGLAS ESTRICTAS:
1. NUNCA traduzcas las etiquetas de color o formato (ej. <br>, <yellow>, <red>, \n).
2. NUNCA traduzcas nombres de variables entre corchetes o llaves (ej. {{player_name}}).
3. GLOSARIO: Mantén estas palabras en inglés: Arks, Falspawn, Monomate, Photon.
4. FORMATO DE SALIDA: Debes devolver ÚNICAMENTE un objeto JSON donde la clave sea el número original y el valor sea LA TRADUCCIÓN FINAL EN ESPAÑOL en texto plano. (Ejemplo: {{"0": "Traducción", "1": "Traducción"}}).

JSON A TRADUCIR:
{json.dumps(chunk_dict, ensure_ascii=False)}
"""

        translated_dict = {}
        try:
            log(f"Traduciendo lote de {len(chunk)} textos (con contexto inyectado)...")
            response = completion(
                model="gemini/gemini-1.5-flash-latest", 
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}, 
                fallbacks=["groq/llama-3.1-8b-instant"],
                num_retries=2
            )
            
            result_text = response.choices[0].message.content
            translated_dict = json.loads(result_text)
            
        except Exception as e:
            log(f"Error en IA o formato JSON inválido. Saltando lote: {str(e)[:100]}")
            translated_dict = {str(idx): txt for idx, (txt, ctx) in enumerate(chunk)}

        for idx_str, (src_text, _) in enumerate(chunk):
            idx_str = str(idx_str)
            dst = translated_dict.get(idx_str, src_text)
            
            # Protección extra por si la IA devuelve un objeto en lugar de un string
            if isinstance(dst, dict):
                dst = dst.get("texto_a_traducir", src_text) 
                
            dst = apply_release_sed(dst)
            out_map[src_text] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src_text, src_lang, dst))
            
        conn.commit()
        time.sleep(2) 
        
    return out_map

# === NUEVO PROCESADOR DE ARCHIVOS CON VALIDACIÓN ===
def process_translations(conn, git_ready):
    files_to_process = list(CSV_DIR.rglob("*.csv"))
    files_modified = 0

    for file_path in files_to_process:
        rows = read_csv_rows(file_path)
        if not rows: continue

        # 1. Validar integridad estructural del archivo
        malformed_rows = [r for r in rows if len(r) < 4]
        if malformed_rows:
            log(f"⚠️ ADVERTENCIA: El archivo {file_path.name} está mal diseñado o corrupto. Contiene {len(malformed_rows)} líneas sin las 4 columnas requeridas.")

        # 2. Extraer el contexto (Grupo 0)
        context_jp_map = {}
        for row in rows:
            if len(row) >= 4 and row[1] == "0":
                clave_unica = f"{row[0]}_{row[2]}" # Ej. CharaName_name01
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
                    # Buscamos si existe contexto para este ID específico
                    contexto_encontrado = context_jp_map.get(f"{section}_{id_val}", "")
                    texts_to_translate.append((text, contexto_encontrado))
                
            row_meta.append((row, text, needs))

        # Pasamos la lista de tuplas (texto, contexto) y eliminamos duplicados
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
                log(f"Progreso alcanzado ({files_modified} archivos). Asegurando cambios en GitHub...")
                push_to_github()

    if git_ready and files_modified > 0:
        push_to_github()
