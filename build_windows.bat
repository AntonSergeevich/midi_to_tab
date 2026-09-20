@echo off
chcp 65001 >nul
REM Сборка MidiToTab.exe. Запускать из папки проекта, в активированном venv.

echo === Установка зависимостей ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo.
echo === Сборка ===
pyinstaller --noconfirm --clean midi2tab.spec

echo.
echo Готово. Приложение здесь: dist\MidiToTab\MidiToTab.exe
pause
