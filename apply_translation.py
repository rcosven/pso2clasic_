"""Translate group=1 rows to Spanish and move files to listo/."""
import csv
import shutil
from pathlib import Path

INPUT_DIR = Path(r"C:\Grok traduccion\archivos a traducir")
OUTPUT_DIR = Path(r"C:\Grok traduccion\listo")

TRANSLATIONS: dict[str, dict[str, str]] = {
    "st_010490_om.csv": {
        "name01": "¿?",
        "name02": "¿?",
        "st_010490_0010": (
            "¿Eh? ¿Qué pasa, compañero? ¿Qué es eso?<br>"
            "¿Algún tipo de patrón...? ¿No, letras?"
        ),
        "st_010490_0020": (
            "Mmm... ¿Qué dirá esto?<br>"
            "Parece que tiene cierta estructura..."
        ),
        "st_010490_0030": (
            "Ese lilliputiano lo estaba leyendo, ¿verdad?<br>"
            "¡Oh! Entonces, si lo seguimos..."
        ),
        "st_010490_0040": (
            "Espera, ya se fue. Maldición... Supongo que los lilliputianos pueden<br>"
            "leer esto.<br>"
            "¿Ojalá pudieran traducírnoslo?"
        ),
        "st_010490_0050": (
            "Pero no entendemos nada de lo que dicen<br>"
            "de todos modos, así que eso no<br>"
            "serviría de nada."
        ),
        "st_010490_0060": (
            "Bueno, compañero. ¡Sigamos adelante y busquemos<br>"
            "otra cosa!"
        ),
        "st_010490_0070": (
            "¿Eh? ¿Eres tú, <%me>?<br>"
            "¿Y tú eres... Afin?"
        ),
        "st_010490_0080": (
            "Qué coincidencia.<br>"
            "Parece que algo te preocupa.<br>"
            "¿Qué sucede?"
        ),
        "st_010490_0090": (
            "Ah, eres la chica de antes.<br>"
            "Emmm... ¿Fourie, verdad?"
        ),
        "st_010490_0100": (
            "Sí, soy yo.<br>"
            "Nunca pensé que nos veríamos<br>"
            "en un lugar como este."
        ),
        "st_010490_0110": (
            "¿Eh? ¿Son jeroglíficos? Mmm...<br>"
            "Se parece mucho a los garabatos que dibujan<br>"
            "estos pequeños."
        ),
        "st_010490_0120": "¿Eh, pueden leer esto?",
        "st_010490_0130": (
            "Dicen que vayamos por aquí.<br>"
            "¿Habrá algo al otro lado?"
        ),
        "st_010490_0140": (
            "¡Ah, espera! ¡Por favor, espera!<br>"
            "¡Yo también voy, lo prometo!"
        ),
        "st_010490_0150": (
            "Uh, se fueron. Esa chica está completamente<br>"
            "a merced de ellos."
        ),
        "st_010490_0160": (
            "En fin, ¿qué quieres hacer, compañero?<br>"
            "¿Seguir adelante? Ese agujero es demasiado<br>"
            "estrecho para..."
        ),
        "st_010490_0170": "¡¿Q-Q-Qué estás haciendo!?",
        "st_010490_0180": "¡Lo volé en pedazos!",
        "st_010490_0190": "¡Eso no es lo que quise decir! ¡Es obvio!",
        "st_010490_0200": "¡Te estoy preguntando por qué harías algo tan loco!",
        "st_010490_0210": (
            "¡Porque si no, no podríamos pasar!<br>"
            "¿Ves? ¡Ahora está abierto de par en par!"
        ),
        "st_010490_0220": "¡Ah, ahí estás! ¡Espérame!",
        "st_010490_0230": (
            "Es bastante, eh, enérgica.<br>"
            "¿Quieres seguirlos? Te dejo la decisión a ti.<br>"
            "Estoy demasiado agotado para decidir..."
        ),
        "st_010490_0240": "A.P.238/3/24/10:30",
    },
    "st_010520_om.csv": {
        "name01": "¿?",
        "name02": "¿?",
        "st_010520_0010": "¡Oh, <%me>!<br>Así que tú también viniste aquí.",
        "st_010520_0020": (
            "Ese arma está aquí, ¿verdad? También hay rumores de que<br>"
            "ese tipo enmascarado fue visto por aquí."
        ),
        "st_010520_0030": (
            "Oye, eh, esta conversación me está poniendo nerviosa.<br>"
            "Apurémonos y vámonos de aquí."
        ),
        "st_010520_0040": "No quiero quedarme en un lugar tan aterrador como este.",
        "st_010520_0050": (
            "Lamentablemente para ti, estamos aquí para registrar cada<br>"
            'rincón de este "lugar aterrador", así que...<br>'
            "¿Quieres volver solo, o qué?"
        ),
        "st_010520_0060": (
            "¡N-No me voy!<br>"
            "¡Dejar solos a <%me> y a Zeno<br>"
            "sería demasiado peligroso!"
        ),
        "st_010520_0070": "Ajá. Claro que sí.",
        "st_010520_0080": (
            "Bueno, ya que nos encontramos,<br>"
            "sigamos todos juntos... ¿vale?"
        ),
        "st_010520_0090": "Aunque la pregunta es... ¿Cómo vamos a buscar?",
        "st_010520_0100": (
            "Supongo que por ahora seguiremos adelante. Quizá encontremos<br>"
            "algo por el camino."
        ),
        "st_010520_0110": (
            "Deberíamos poder saber de un vistazo si un draconiano ha perdido<br>"
            "la cabeza. Si estamos alerta, estaremos bien."
        ),
        "st_010520_0120": (
            "¡Pero tomaremos la ruta menos peligrosa, ¿de acuerdo? ¡La menos<br>"
            "aterradora!"
        ),
        "st_010520_0130": "(Espera,) (<%me>.)",
        "st_010520_0140": "¡¿U-Un draconiano!? ¿Es un enemigo!?",
        "st_010520_0150": "Cálmate, Echo. No parece que lo sea.",
        "st_010520_0160": (
            "(Te expreso) (mi gratitud.)<br>"
            "(Has salvado) (nuestras vidas.)"
        ),
        "st_010520_0170": (
            "(Y) (tengo un mensaje) (para ti.)<br>"
            "(Hay alguien que te espera)<br>"
            "(si avanzas) (al oeste de aquí.)"
        ),
        "st_010520_0180": (
            "(Me dijeron) (que había algo)<br>"
            "(que debía confiarte.)"
        ),
        "st_010520_0190": (
            "(Mi deber está cumplido.) (Partiré.)<br>"
            "(Eres libre) (de ir o no.)"
        ),
        "st_010520_0200": (
            "Uf, eso fue intenso. Así es como hablan los draconianos,<br>"
            "¿eh? Bueno, supongo que es más telepatía que habla."
        ),
        "st_010520_0210": "En fin, ¿aparentemente hay alguien esperando al oeste de aquí?",
        "st_010520_0220": (
            "No parecía una trampa. ¿Qué quieres hacer?<br>"
            "Dejaré la ruta en tus manos."
        ),
        "st_010520_0230": "Y, eh... ¿Echo? ¿Cuánto tiempo piensas aferrarte a mí?",
        "st_010520_0240": (
            "¿Eh? ¡N-No me estoy aferrando a ti! Te estaba sujetando<br>"
            "porque pensé que podrías abalanzarte sobre él."
        ),
        "st_010520_0250": "¿Qué clase de idiota crees que soy, exactamente?",
        "st_010520_0260": "A.P.238/3/25/10:00",
    },
}


def process_file(filename: str) -> None:
    input_path = INPUT_DIR / filename
    output_path = OUTPUT_DIR / filename
    translations = TRANSLATIONS[filename]

    with input_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    updated = 0
    for row in rows:
        if row["group"] == "1" and row["id"] in translations:
            row["text"] = translations[row["id"]]
            updated += 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["section", "group", "id", "text"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)

    input_path.unlink()
    print(f"{filename}: {updated} filas group=1 -> {output_path}")


if __name__ == "__main__":
    for name in TRANSLATIONS:
        process_file(name)