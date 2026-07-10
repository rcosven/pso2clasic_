# En lugar de leer todos los archivos en cada comando:
# Carga los índices una vez al iniciar el bot
INDEX = {}

def precargar_indices():
    for f in LISTO_DIR.glob("*.csv"):
        # Solo mapea los IDs para búsqueda rápida
        with open(f, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                INDEX[row['id']] = f.name 

@bot.tree.command(name="buscar_id", description="Busca un ID específico")
async def buscar_id(interaction: discord.Interaction, id_buscado: str):
    archivo = INDEX.get(id_buscado)
    if archivo:
        await interaction.response.send_message(f"El ID {id_buscado} está en el archivo {archivo}")
    else:
        await interaction.response.send_message("ID no encontrado.")