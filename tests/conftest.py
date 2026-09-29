import os

# Нейросеть долей (Beat This, ~2.5 с на минуту звука) в общих тестах не
# нужна: разбор проверяется и по librosa, а сама сеть -- в test_beatnet.py
# на подменённой модели. Иначе тесты идут минуты вместо секунд.
os.environ.setdefault("NASLUX_BEATS", "off")
