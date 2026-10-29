# Видеоинструкция NASLUX (`/guide`)

Ролик снимается с настоящего сайта на демо-аккаунте — после заметных
изменений интерфейса его можно переснять теми же шагами.

1. Демо-песня (свои партии, 120 BPM): `python3 synth.py <папка>/audio`
2. Демо-аккаунт с подпиской «Студия», трек, партии, обложка:
   `python3 seed.py <папка>/data <папка>/audio`, затем сделать пользователя
   подписчиком (`tier=studio_month`, `paid_until` в будущем).
3. Сайт на этих данных, со «Студией готова» и без пробы YuE2:
   `NASLUX_YUE2=0 RUNPOD_API_KEY=rp-demo NASLUX_WORKER_ENDPOINT=ep-demo
   BEGET_AI_API_KEY=sk-demo-not-real-key-000000 MIDI2TAB_DATA=<папка>/data
   python3 -m uvicorn web.app:app --port 8783`
   («Дописать с ИИ» и «Сочинить» в записи подменяются готовыми ответами.)
4. Съёмка (Playwright, его версия — под браузер в контейнере):
   `python3 record.py <папка>` — компьютер 1280×720 и телефон 390×844.
5. Сборка в `web/media/naslux-guide.mp4`: заставка, компьютер, телефон,
   концовка. Фоном — инструментал NASLUX, сделанный в нашей же студии
   (YuE2, `scripts/yue2_job_naslux_instrumental.json`, зерно 42), громкость
   выровнена loudnorm до −15 LUFS, плавные вход и выход:
   `ffmpeg -i video.mp4 -i music.mp3 -filter_complex
   "[1:a]atrim=0:DUR,loudnorm=I=-15:TP=-1.5:LRA=11,afade=t=in:st=0:d=1.5,afade=t=out:st=DUR-4:d=4[a]"
   -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 160k -shortest -movflags +faststart naslux-guide.mp4`
