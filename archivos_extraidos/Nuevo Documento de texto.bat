@echo off
setlocal enabledelayedexpansion

set "DESTINO=%~dp0"

echo Buscando archivos CSV...

for /r "%DESTINO%" %%F in (*.csv) do (
    if /I not "%%~dpF"=="%DESTINO%" (
        echo Copiando: %%~nxF
        copy /Y "%%F" "%DESTINO%" >nul
    )
)

echo.
echo Proceso terminado.
pause