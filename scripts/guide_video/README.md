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
   концовка. Фоном — гимн NASLUX в ню-метале (Suno v6 через GenAPI,
   `scripts/suno_job_naslux_numetal.json`, вторая версия), продлённый склейкой
   (acrossfade с 24-й секунды), громкость loudnorm до −16 LUFS:
   `ffmpeg -i video.mp4 -i music.mp3 -filter_complex
   "[1:a]atrim=0:DUR,loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d=1.5,afade=t=out:st=DUR-4:d=4[a]"
   -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 160k -shortest -movflags +faststart naslux-guide.mp4`

Английская версия (`web/media/naslux-guide-en.mp4`, страница /guide сама
берёт её при английском интерфейсе): `GUIDE_LANG=en` для `seed.py` и
`record.py` — английский интерфейс, подписи, демо-трек и тексты; заставка и
концовка — те же команды с английским текстом. Сервер для записи — с
`MIDI2TAB_SECRET=x` (им подписан вход демо-аккаунта) и `NASLUX_ACE_FALLBACK=1`
(иначе под «Создать» висит «Песни с нуля скоро появятся»). `record.py`
пишет в `raw/videos.json` секунды шагов — по ним главы на странице
(+3,1 с: заставка 3,5 с минус обрезка 0,4 с).
