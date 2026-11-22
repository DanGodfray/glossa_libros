@echo off
REM Inicia GlossaLibros usando el entorno virtual.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo No existe el entorno virtual. Ejecuta primero instalar.bat
    pause
    exit /b 1
)

REM Configuracion opcional: copia .env.example como .env y completa los valores.
REM Cada linea NOMBRE=valor se carga como variable de entorno (las que empiezan con # se ignoran).
if exist .env (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
)

.venv\Scripts\python.exe app.py
pause
