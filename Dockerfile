FROM python:3.11-slim
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY discord_bot.py .
# Nota: No copies las carpetas de datos (CSV) aquí, 
# el bot las leerá directamente desde el volumen de Railway.
CMD ["python", "discord_bot.py"]