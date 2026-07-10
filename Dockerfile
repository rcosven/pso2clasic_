#!/bin/sh
# start.sh mejorado
echo "Iniciando procesos..."
python translate_missing.py &
python discord_bot.py &
wait -n
exit $?