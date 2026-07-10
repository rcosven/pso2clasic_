FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Añadimos el -u aquí abajo:
CMD ["python", "-u", "discord_bot.py"]