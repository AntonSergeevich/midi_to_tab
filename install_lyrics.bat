@echo off
chcp 65001 >nul
REM Распознавание текста песен (Whisper).
REM Запускать из папки проекта, в активированном venv.

echo Модель скачается при первом запуске: small -- около 480 МБ.
echo.
python -m pip install faster-whisper
if errorlevel 1 goto fail

echo.
python -c "from faster_whisper import WhisperModel; print('Whisper готов')"
if errorlevel 1 goto fail

echo.
echo Готово. В плеере появится кнопка «Распознать текст».
pause
exit /b 0

:fail
echo.
echo Установка не удалась. Пришлите текст ошибки.
pause
exit /b 1
