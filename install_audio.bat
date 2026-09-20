@echo off
chcp 65001 >nul
REM Установка распознавания аудио (стем -> MIDI).
REM Запускать из папки проекта, в активированном venv, ПОСЛЕ requirements.txt

echo Внимание: pip покажет красные строки про tensorflow и resampy.
echo Это ожидаемо и ни на что не влияет - подробности в requirements-audio.txt.
echo.
echo === Зависимости распознавания ===
python -m pip install -r requirements-audio.txt
if errorlevel 1 goto fail

echo.
echo === Сама модель (без разрешения зависимостей -- см. requirements-audio.txt) ===
python -m pip install --no-deps basic-pitch
if errorlevel 1 goto fail

echo.
echo === Проверка ===
python -c "from basic_pitch.inference import predict; import basic_pitch; print('ONNX доступен:', basic_pitch.ONNX_PRESENT)"
if errorlevel 1 goto fail

echo.
echo Готово. Теперь приложение принимает аудио-стемы.
pause
exit /b 0

:fail
echo.
echo Установка не удалась. Пришлите текст ошибки.
pause
exit /b 1
