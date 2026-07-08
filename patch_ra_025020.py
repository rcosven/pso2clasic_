import csv
from pathlib import Path

FILE = Path(r"C:\Grok traduccion\listo\ra_025020_om.csv")

FIXES = {
    "ra_025020_0010": "<%me><br>... Llevaba mucho tiempo esperando tu llegada.",
    "ra_025020_0020": (
        "Je, ¿qué te parece mi imitación de Xion?<br>"
        "Capté el estilo a la perfección, ¿verdad?"
    ),
    "ra_025020_0030": (
        "Bueno, técnicamente soy una copia suya, así que<br>"
        "no sería tan raro que hubiera acabado<br>"
        "siendo exactamente como ella."
    ),
    "ra_025020_0040": (
        'Pero creo que Xion ya hizo bastante ese papel de<br>'
        '"misteriosa y digna" por los dos.<br>'
        "Prefiero hablar como, ya sabes, una persona normal."
    ),
    "ra_025020_0050": (
        "En fin, vayamos al grano: por qué te llamé<br>"
        "aquí. Hay algo que quería decirte<br>"
        "en persona."
    ),
    "ra_025020_0060": "Se trata de...<br>Bueno, se trata de Matoi.",
    "ra_025020_0070": (
        "Si soy totalmente sincero contigo, no tengo<br>"
        "claro quién o qué es en realidad. No tenemos<br>"
        "ningún dato sobre ella."
    ),
    "ra_025020_0080": (
        "Seguro que tú también sabes que alguien<br>"
        "con un nivel de habilidad tan descomunal<br>"
        "llama la atención a leguas, ¿verdad?"
    ),
    "ra_025020_0090": (
        "Y no me refiero solo a la base de datos de ARKS.<br>"
        "Ni siquiera figura en los registros de Xion.<br>"
        "Es como si alguien la hubiera borrado a propósito."
    ),
    "ra_025020_0100": (
        "Si hubiera sido Lutero, lo entendería.<br>"
        "Pero ni siquiera ÉL parecía saber<br>"
        "nada de ella."
    ),
    "ra_025020_0110": (
        "Y si no fue él, solo se me ocurre<br>"
        "un otro posible sospechoso..."
    ),
    "ra_025020_0120": (
        "Tuvo que ser Xion. Debió<br>"
        "borrar todo rastro de Matoi."
    ),
    "ra_025020_0130": (
        "Y no solo borraron a Matoi. Xion<br>"
        "parece haber eliminado todos los registros de hace<br>"
        "más o menos diez años."
    ),
    "ra_025020_0135": (
        "Tampoco son solo registros físicos. Nadie en<br>"
        "ARKS parece tener recuerdos claros de<br>"
        "esa época."
    ),
    "ra_025020_0140": (
        "No es el tipo de datos que podemos rescatar<br>"
        "o rastrear. Ha desaparecido<br>"
        "por completo. Puf. Fin."
    ),
    "ra_025020_0150": (
        "Algo importante debió ocurrir hace diez<br>"
        "años. Pero no podemos recuperar recuerdos que<br>"
        "ya no existen..."
    ),
    "ra_025020_0160": (
        "Lo que NECESITAMOS hacer es regresar directamente<br>"
        "y verlo todo con nuestros propios ojos."
    ),
}

with FILE.open(encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))

updated = 0
for row in rows:
    if row["group"] == "1" and row["id"] in FIXES:
        row["text"] = FIXES[row["id"]]
        updated += 1

with FILE.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.DictWriter(
        fh, fieldnames=["section", "group", "id", "text"], lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)

print(f"Corregidas {updated} filas en {FILE}")