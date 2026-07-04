import json
import time
import sqlite3
import litellm
from litellm import completion
from config import CACHE_DB
from utils import log, apply_release_sed, should_skip_translate

litellm.suppress_debug_info = True 

def init_cache():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (src TEXT, lang TEXT, dst TEXT, PRIMARY KEY(src, lang))")
    conn.commit()
    return conn

def batch_translate(conn, text_data: list[tuple[str, str]], src_lang: str) -> dict[str, str]:
    out_map: dict[str, str] = {}
    pending: list[tuple[str, str]] = []
    
    for text, context in text_data:
        if should_skip_translate(text):
            out_map[text] = text
            continue
        row = conn.execute("SELECT dst FROM cache WHERE src=? AND lang=?", (text, src_lang)).fetchone()
        if row: out_map[text] = row[0]
        else: pending.append((text, context))
        
    if not pending: return out_map
    chunk_size = 15
    
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i:i + chunk_size]
        chunk_dict = {str(idx): {"contexto_original": ctx, "texto_a_traducir": txt} for idx, (txt, ctx) in enumerate(chunk)}
        
        prompt = f"""Eres un traductor profesional de videojuegos (PSO2).
Recibirás un objeto JSON. 'contexto_original' es japonés (NO TRADUCIR). 'texto_a_traducir' es lo que DEBES traducir al español.
REGLAS:
1. No traduzcas etiquetas HTML/XML ni corchetes.
2. Mantén en inglés: Arks, Falspawn, Monomate, Photon.
3. Devuelve SOLO un JSON con formato: {{"0": "Traduccion1", "1": "Traduccion2"}}

JSON A TRADUCIR:
{json.dumps(chunk_dict, ensure_ascii=False)}
"""
        translated_dict = {}
        exito = False
        intentos_limite = 0
        
        # Bucle de reintentos inteligentes para evadir el límite de peticiones (429)
        while not exito and intentos_limite < 3:
            try:
                log(f"Traduciendo lote de {len(chunk)} textos...")
                response = completion(
                    model="gemini/gemini-2.5-flash", 
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}, 
                    fallbacks=["groq/llama-3.1-8b-instant"],
                    num_retries=2
                )
                translated_dict = json.loads(response.choices[0].message.content)
                exito = True # Si llega aquí, todo salió bien
                
            except Exception as e:
                error_str = str(e).lower()
                # Si el error es por límite de velocidad, esperamos 60s y volvemos a intentar
                if "429" in error_str or "quota" in error_str or "rate limit" in error_str:
                    intentos_limite += 1
                    log(f"⚠️ Límite de API alcanzado. Pausando 60 segundos (Intento {intentos_limite}/3)...")
                    time.sleep(60)
                else:
                    # Si es un error diferente (JSON roto, IA caída), saltamos el lote
                    log("Error desconocido en IA. Saltando lote...")
                    translated_dict = {str(idx): txt for idx, (txt, ctx) in enumerate(chunk)}
                    exito = True 

        # Si después de 3 pausas de 60s seguimos bloqueados, devolvemos el original
        if not translated_dict:
            translated_dict = {str(idx): txt for idx, (txt, ctx) in enumerate(chunk)}

        for idx_str, (src_text, _) in enumerate(chunk):
            idx_str = str(idx_str)
            dst = translated_dict.get(idx_str, src_text)
            if isinstance(dst, dict): dst = dst.get("texto_a_traducir", src_text) 
            dst = apply_release_sed(dst)
            out_map[src_text] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src_text, src_lang, dst))
        
        conn.commit()
        time.sleep(4) # Subimos el descanso base a 4 segundos para un flujo estable
        
    return out_map
