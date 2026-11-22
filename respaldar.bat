@echo off
REM Guarda la base de datos y las fotos en un zip con fecha dentro de la carpeta "respaldos".
REM Cierra la app (iniciar.bat) antes de respaldar, para que la base de datos quede completa.
cd /d "%~dp0"

if not exist inventario.db (
    echo No hay inventario.db que respaldar.
    pause
    exit /b 1
)
if not exist respaldos mkdir respaldos

REM tar (incluido en Windows 10 y 11) crea un zip que tambien se abre bien en Linux, p. ej. PythonAnywhere
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set FECHA=%%d
set ARCHIVO=respaldos\glossalibros_%FECHA%.zip
tar -a -c -f "%ARCHIVO%" inventario.db static/fotos
if errorlevel 1 (
    echo Hubo un error al crear el respaldo.
    pause
    exit /b 1
)

echo Respaldo creado: %CD%\%ARCHIVO%
echo.
echo Para restaurar en otro PC: descomprime el zip dentro de la carpeta de la app
echo y acepta reemplazar los archivos.
pause
