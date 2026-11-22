@echo off
REM Crea el entorno virtual e instala las dependencias. Ejecutar UNA vez en cada PC.
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (
    where python >nul 2>nul && set PY=python
)
if not defined PY (
    echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
    echo y marca la casilla "Add python.exe to PATH" durante la instalacion.
    pause
    exit /b 1
)

if exist .venv (
    echo Borrando entorno virtual anterior...
    rmdir /s /q .venv
)

echo Creando entorno virtual...
%PY% -m venv .venv || goto error

echo Instalando dependencias...
.venv\Scripts\python.exe -m pip install --upgrade pip || goto error
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto error

echo.
echo Listo. Ahora haz doble clic en iniciar.bat
pause
exit /b 0

:error
echo.
echo Hubo un error durante la instalacion. Revisa los mensajes de arriba.
pause
exit /b 1
