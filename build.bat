@echo off
rem Baut release\GameTracker.exe (benoetigt Python 3.12+)
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :error
python -m PyInstaller GameTracker.spec --noconfirm --distpath release --workpath build || goto :error
echo.
echo Fertig: release\GameTracker.exe
echo games.json, config.json, waitlist.json und notifications.json neben die exe legen.
goto :eof

:error
echo Build fehlgeschlagen.
exit /b 1
