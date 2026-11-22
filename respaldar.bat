@echo off
REM Guarda la base de datos y las fotos en un zip con fecha dentro de la carpeta "respaldos".
REM Funciona aunque la app este abierta. Se conservan los 10 respaldos mas recientes.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo No existe el entorno virtual. Ejecuta primero instalar.bat
    pause
    exit /b 1
)

.venv\Scripts\python.exe respaldo.py
if errorlevel 1 (
    echo Hubo un error al crear el respaldo.
    pause
    exit /b 1
)

echo.
echo Para restaurar en otro PC: descomprime el zip dentro de la carpeta de la app
echo y acepta reemplazar los archivos.
pause
