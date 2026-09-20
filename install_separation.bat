@echo off
chcp 65001 >nul
REM Установка разделения трека на дорожки (Demucs + PyTorch).
REM Запускать из папки проекта, в активированном venv.

echo Это тяжёлая установка: PyTorch весит около 300 МБ.
echo Веса модели докачаются при первом разделении.
echo.

python -m pip install -r requirements-separation.txt
if errorlevel 1 goto fail

echo.
echo === Проверка ===
python -c "import demucs.separate, torch; print('Demucs готов. Устройство:', 'видеокарта' if torch.cuda.is_available() else 'процессор')"
if errorlevel 1 goto fail

echo.
echo Готово. В приложении появится шаг «Разделение трека на дорожки».
pause
exit /b 0

:fail
echo.
echo Установка не удалась. Пришлите текст ошибки.
pause
exit /b 1
