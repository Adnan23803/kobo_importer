@echo off
setlocal
cd /d %~dp0

echo ============================================================
echo   Construction de Kobo Importer
echo ============================================================
echo.

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

echo [1/4] Installation des dependances...
%PY% -m pip install --upgrade pip >nul
%PY% -m pip install -r requirements-dev.txt || goto :erreur

echo [2/4] Verification du noyau, des ameliorations et de l'interface...
%PY% tests\test_koboimp.py || goto :erreur
%PY% tests\test_ameliorations.py || goto :erreur
%PY% tests\smoke_ui.py || goto :erreur

echo [3/4] Nettoyage des constructions precedentes...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [4/4] Construction (mode dossier, sans UPX)...
%PY% -m PyInstaller --noconfirm --clean KoboImporter.spec || goto :erreur

echo.
echo ============================================================
echo   Termine. Application : dist\KoboImporter\KoboImporter.exe
echo.
echo   Pour distribuer, deux possibilites :
echo     - compresser tout le dossier dist\KoboImporter ;
echo     - ou construire l'installeur avec installer.iss (Inno Setup),
echo       ce qui est preferable sur des postes d'organisation.
echo ============================================================
goto :fin

:erreur
echo.
echo *** La construction a echoue. Voir les messages ci-dessus. ***
exit /b 1

:fin
pause
