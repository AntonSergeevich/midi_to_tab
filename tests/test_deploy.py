"""
Проверки комплекта развёртывания.

Эти файлы нельзя прогнать целиком без сервера, но самое хрупкое в них --
согласованность путей и настроек между юнитом, установщиком и nginx.
Расхождение здесь стоит дорого: оно вылезает только в бою.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def unit() -> str:
    return (DEPLOY / "nasluh.service").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def installer() -> str:
    return (DEPLOY / "install.sh").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nginx() -> str:
    return (DEPLOY / "nginx.conf").read_text(encoding="utf-8")


# ------------------------------------------------------------------- кэши


@pytest.mark.parametrize(
    "variable", ["HOME", "XDG_CACHE_HOME", "NUMBA_CACHE_DIR", "MPLCONFIGDIR"]
)
def test_caches_are_redirected(unit, variable):
    """
    Регрессия: служба падала с "cannot cache function: no locator available".

    ProtectSystem=strict делает файловую систему доступной только для
    чтения, а numba внутри librosa пишет кэш рядом со своим модулем.
    Все кэши обязаны указывать в папку данных -- единственную доступную
    на запись.
    """
    match = re.search(rf"^Environment={variable}=(.+)$", unit, re.M)
    assert match, f"{variable} не задана в юните"
    assert match.group(1).startswith("/opt/nasluh/data"), (
        f"{variable} указывает вне папки данных: {match.group(1)}"
    )


def test_writable_path_covers_cache_targets(unit):
    """Всё, куда служба пишет, должно быть в ReadWritePaths."""
    writable = re.findall(r"^ReadWritePaths=(.+)$", unit, re.M)
    assert writable
    targets = re.findall(r"^Environment=\w+=(/opt/\S+)$", unit, re.M)
    for target in targets:
        assert any(target.startswith(path) for path in writable), (
            f"{target} недоступен для записи"
        )


def test_installer_creates_cache_dir(installer):
    assert "cache" in re.search(r'mkdir -p "\$APP_DIR".+', installer).group(0)


# --------------------------------------------------------------- согласие


def test_single_worker_is_intentional(unit):
    """
    Разбор идёт в пуле потоков внутри процесса: каждый лишний процесс
    множит и пул, и память под PyTorch.
    """
    assert "--workers 1" in unit


def test_memory_limit_present(unit):
    assert re.search(r"^MemoryMax=", unit, re.M)


def test_service_does_not_run_as_root(unit):
    user = re.search(r"^User=(.+)$", unit, re.M)
    assert user and user.group(1) != "root"


# ------------------------------------------------------------------ nginx


def test_upload_limit_matches_app(nginx):
    """
    Предел nginx должен быть не меньше предела приложения, иначе nginx
    отрежет загрузку раньше, чем приложение объяснит причину.
    """
    limit = re.search(r"client_max_body_size (\d+)m", nginx)
    assert limit and int(limit.group(1)) >= 60


def test_long_timeouts_for_slow_jobs(nginx):
    read = re.search(r"proxy_read_timeout\s+(\d+)s", nginx)
    assert read and int(read.group(1)) >= 300


def _active_lines(text: str) -> str:
    """Только действующие строки: в комментариях пример может встречаться."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


def test_no_ipv6_binding(nginx):
    """listen [::] роняет nginx целиком, если IPv6 на сервере выключен."""
    assert "listen [::]" not in _active_lines(nginx)


def test_http2_uses_compatible_syntax(nginx):
    """Отдельная директива http2 требует nginx 1.25.1+."""
    assert not re.search(r"^\s*http2 on;", _active_lines(nginx), re.M)


def test_static_path_matches_installer(nginx, installer):
    """Путь к статике в nginx должен совпадать с тем, куда ставится код."""
    alias = re.search(r"alias (\S+)/web/static/;", nginx)
    assert alias
    assert re.search(rf"APP_DIR={re.escape(alias.group(1))}\b", installer)


# ------------------------------------------------------------ обновление


@pytest.fixture(scope="module")
def updater() -> str:
    return (DEPLOY / "update.sh").read_text(encoding="utf-8")


def test_update_script_exists(updater):
    assert "systemctl restart nasluh" in updater


@pytest.mark.parametrize("script", ["install.sh", "update.sh"])
def test_git_ownership_exception(script):
    """
    Регрессия: папка принадлежит nasluh, git запускается от root, и с
    версии 2.35 он отказывается работать -- "detected dubious ownership".
    """
    text = (DEPLOY / script).read_text(encoding="utf-8")
    assert "safe.directory" in text


def test_update_reinstalls_units(updater):
    """Юниты меняются вместе с кодом -- иначе правки в них не доедут."""
    for unit in ("nasluh.service", "nasluh-cleanup.service", "nasluh-cleanup.timer"):
        assert unit in updater
    assert "daemon-reload" in updater


def test_update_keeps_ownership(updater):
    """После обновления файлы должны остаться у пользователя службы."""
    assert "chown -R nasluh:nasluh" in updater


def test_separation_installer_avoids_cuda():
    """
    Регрессия: pip install demucs тянет torch со всем набором CUDA --
    около двух гигабайт драйверов для видеокарты, которой нет. Диск на
    сорок гигабайт этого не выдержал.
    """
    text = (DEPLOY / "install_separation.sh").read_text(encoding="utf-8")
    assert "download.pytorch.org/whl/cpu" in text
    assert "df --output=avail" in text   # проверка места до установки


# ------------------------------------------------------- консольная админка


def test_admin_tool_runs(tmp_path):
    """
    Инструмент должен работать на пустой базе, а не падать.

    Он нужен как раз тогда, когда что-то пошло не так, -- и падать в
    такой момент ему нельзя.
    """
    from web.storage import Storage

    Storage(str(tmp_path / "app.db"))
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "admin.py"), "список"],
        env={**os.environ, "MIDI2TAB_DATA": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Пользователей пока нет" in result.stdout


def test_admin_tool_grants_rights(tmp_path):
    from web import auth
    from web.storage import Storage

    store = Storage(str(tmp_path / "app.db"))
    user = store.ensure_user(None)
    store.register(user.id, "ivan@mail.ru", auth.hash_password("пароль-12345"))

    result = subprocess.run(
        [sys.executable, str(DEPLOY / "admin.py"), "админ", "ivan@mail.ru"],
        env={**os.environ, "MIDI2TAB_DATA": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    fresh = Storage(str(tmp_path / "app.db")).user(user.id)
    assert fresh.is_admin and fresh.unlimited


def test_admin_tool_reports_missing_database(tmp_path):
    """Понятное сообщение вместо трассировки: инструмент для экстренных случаев."""
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "admin.py"), "список"],
        env={**os.environ, "MIDI2TAB_DATA": str(tmp_path / "нет-такой")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "База не найдена" in result.stderr
    assert "Traceback" not in result.stderr


def test_admin_command_works_on_an_empty_database(tmp_path):
    """
    В первый день учётных записей в базе нет вообще.

    Раньше получался замкнутый круг: права выдаются только существующему
    пользователю, а существующим становишься только зарегистрировавшись
    через сайт. Владелец с чистого сервера в управление не попадал.
    Теперь "админ" заводит запись на месте.
    """
    import subprocess
    import sys
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = tmp_path / "data"
    data.mkdir()

    sys.path.insert(0, root)
    from web.storage import Storage

    Storage(str(data / "app.db"))

    result = subprocess.run(
        [sys.executable, os.path.join(root, "deploy", "admin.py"),
         "админ", "vladelec@naslux.ru", "--note", "владелец"],
        env={**os.environ, "MIDI2TAB_DATA": str(data)},
        input="длинный-пароль-99\nдлинный-пароль-99\n",
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Создана учётная запись" in result.stdout

    store = Storage(str(data / "app.db"))
    owner = store.user_by_email("vladelec@naslux.ru")
    assert owner is not None
    assert owner.is_admin and owner.unlimited and owner.registered

    # Повторный вызов не должен плодить двойников
    again = subprocess.run(
        [sys.executable, os.path.join(root, "deploy", "admin.py"),
         "админ", "vladelec@naslux.ru"],
        env={**os.environ, "MIDI2TAB_DATA": str(data)},
        capture_output=True, text=True, timeout=60,
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(store.all_users()) == 1


# --------------------------------------------- замер каскада разделения


def test_separation_compare_installer_reuses_cpu_torch():
    """
    Установщик замера обязан требовать CPU-torch, а не тянуть свой.

    audio-separator сам просит torch>=2.3,<3 -- версия достаточно
    широкая, чтобы pip не переустанавливал уже стоящий. Но если порядок
    перепутать или пропустить проверку, можно неожиданно получить сборку
    с CUDA поверх уже поставленной CPU-версии, а это лишние гигабайты на
    сервере, для которого их уже один раз считали впритык.
    """
    text = (DEPLOY / "install_separation_compare.sh").read_text(encoding="utf-8")
    assert "cuda.is_available" in text          # проверяет, что torch уже CPU
    assert "install_separation.sh" in text      # и просит поставить его сначала
    assert "df --output=avail" in text          # место проверяется, как везде


def test_separation_compare_installer_adds_audioread():
    """
    Регрессия: audio-separator 0.47.0 использует audioread, но не
    объявляет его своей зависимостью.

    uvr_lib_v5/spec_utils.py делает безусловный "import audioread" прямо
    при загрузке модуля -- он попадает в путь импорта уже при первом же
    создании Separator(). На реальном сервере пакет встал командой pip
    без единой ошибки, а первый же вызов рухнул ModuleNotFoundError.
    Проверено дважды на живом сервере, прежде чем нашлась причина.
    """
    text = (DEPLOY / "install_separation_compare.sh").read_text(encoding="utf-8")
    assert "install --no-cache-dir audioread" in text


def test_compare_separation_script_has_correct_shape():
    """
    Скрипт замера существует, исполняем и его CLI разбирается без сети.

    Сама separation здесь не проверяется -- для неё нужны настоящие веса
    моделей, которые качаются с сети при первом запуске. Но то, что
    аргументы командной строки не рассыпаются и модуль вообще
    импортируется, проверить можно и без него.
    """
    script = ROOT / "scripts" / "compare_separation.py"
    assert script.is_file()
    assert os.access(script, os.X_OK)

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--vocal-model" in result.stdout


def test_compare_separation_reports_clean_errors_not_tracebacks(tmp_path):
    """
    Регрессия: ошибка шага пряталась под полным трейсбеком.

    Шаг печатает свой JSON-отчёт ДО того, как может упасть кодом
    возврата -- родитель раньше смотрел на код возврата раньше JSON, и
    понятное "модели нет в списке" пряталось под сырым stderr. Проверяем
    целиком, настоящим подпроцессом, с поддельными demucs и
    audio_separator -- так же, как это в итоге отработает на сервере,
    просто без тяжёлых весов.
    """
    fake_root = tmp_path / "fake_libs"
    (fake_root / "demucs").mkdir(parents=True)
    (fake_root / "demucs" / "__init__.py").write_text("", encoding="utf-8")
    (fake_root / "demucs" / "separate.py").write_text(
        '''
import os

def main(argv):
    out_dir = argv[argv.index("--out") + 1]
    audio = argv[-1]
    stem = os.path.splitext(os.path.basename(audio))[0]
    target = os.path.join(out_dir, "htdemucs_6s", stem)
    os.makedirs(target, exist_ok=True)
    for name in ("guitar", "vocals", "bass", "drums", "piano", "other"):
        open(os.path.join(target, f"{name}.wav"), "wb").write(b"RIFFfake")
''',
        encoding="utf-8",
    )
    sep_pkg = fake_root / "audio_separator" / "separator"
    sep_pkg.mkdir(parents=True)
    (fake_root / "audio_separator" / "__init__.py").write_text("", encoding="utf-8")
    (sep_pkg / "__init__.py").write_text(
        '''
class Separator:
    def __init__(self, output_dir=None, output_format="WAV", model_file_dir=None):
        self.output_dir = output_dir

    def list_supported_model_files(self):
        return {"MDXC": {"BS-Roformer": {"filename": "model_bs_roformer_ep_317_sdr_12.9755.ckpt"}}}

    def load_model(self, model_filename):
        self.model = model_filename

    def separate(self, audio_path):
        import os
        os.makedirs(self.output_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(audio_path))[0]
        vocals = f"{base}_(Vocals)_model.wav"
        instr = f"{base}_(Instrumental)_model.wav"
        open(os.path.join(self.output_dir, vocals), "wb").write(b"RIFFfake")
        open(os.path.join(self.output_dir, instr), "wb").write(b"RIFFfake")
        return [vocals, instr]
''',
        encoding="utf-8",
    )

    audio = tmp_path / "трек.mp3"
    audio.write_bytes(b"fake")
    out_dir = tmp_path / "сравнение"

    env = {**os.environ, "PYTHONPATH": str(fake_root)}
    script = ROOT / "scripts" / "compare_separation.py"

    # Счастливый путь: обе дорожки находятся, отчёт собирается
    good = subprocess.run(
        [sys.executable, str(script), str(audio), "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert good.returncode == 0, good.stdout + good.stderr
    guitar_a = out_dir / "a_htdemucs" / "htdemucs_6s" / "трек" / "guitar.wav"
    assert guitar_a.is_file()
    report = json.loads((out_dir / "отчёт.json").read_text(encoding="utf-8"))
    assert report["а_htdemucs"]["ok"] is True
    assert report["б_демукс_по_остатку"]["ok"] is True

    # Ошибочный путь: неизвестная модель -- сообщение чистое, без трейсбека
    bad_out = tmp_path / "сравнение_ошибка"
    bad = subprocess.run(
        [sys.executable, str(script), str(audio),
         "--vocal-model", "неизвестная.ckpt", "--out-dir", str(bad_out)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert "в списке audio-separator нет" in bad.stdout
    assert "Traceback" not in bad.stdout
    bad_report = json.loads((bad_out / "отчёт.json").read_text(encoding="utf-8"))
    assert bad_report["б_снятие_вокала"]["ok"] is False
    assert "неизвестная.ckpt" in bad_report["б_снятие_вокала"]["error"]
    assert "Traceback" not in bad_report["б_снятие_вокала"]["error"]


# ------------------------------------------------------- вебхук автодеплоя

sys.path.insert(0, str(DEPLOY))
import webhook_deploy  # noqa: E402 -- путь добавлен строкой выше


def hmac_hex(secret: str, body: bytes) -> str:
    import hashlib
    import hmac as hmac_mod

    return hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_deploy_webhook_script_is_executable():
    assert os.access(DEPLOY / "webhook_deploy.py", os.X_OK)


def test_deploy_webhook_accepts_correctly_signed_push():
    secret = "тестовый-секрет"
    body = json.dumps({"ref": "refs/heads/claude/epic-mccarthy-sl30hz"}).encode()
    header = "sha256=" + hmac_hex(secret, body)
    assert webhook_deploy.verify_signature(secret, body, header) is True


def test_deploy_webhook_rejects_wrong_signature():
    body = b'{"ref": "refs/heads/claude/epic-mccarthy-sl30hz"}'
    assert webhook_deploy.verify_signature("секрет", body, "sha256=" + "0" * 64) is False


def test_deploy_webhook_rejects_missing_or_malformed_header():
    body = b"{}"
    assert webhook_deploy.verify_signature("секрет", body, None) is False
    assert webhook_deploy.verify_signature("секрет", body, "не-sha256=подпись") is False


def test_deploy_webhook_refuses_to_verify_without_a_secret():
    """Пустой секрет (не задан на сервере) не должен принимать вообще ничего."""
    body = b"{}"
    header = "sha256=" + hmac_hex("", body)
    assert webhook_deploy.verify_signature("", body, header) is False


def test_deploy_webhook_ignores_ping_event():
    """
    Регрессия: GitHub шлёт "ping" сразу при добавлении вебхука -- сама
    настройка вебхука не должна вызывать деплой.
    """
    deploy, reason = webhook_deploy.should_deploy("ping", {"ref": "refs/heads/claude/epic-mccarthy-sl30hz"})
    assert deploy is False
    assert "ping" in reason


def test_deploy_webhook_ignores_other_branches():
    deploy, reason = webhook_deploy.should_deploy(
        "push", {"ref": "refs/heads/какая-то-другая-ветка"}
    )
    assert deploy is False


def test_deploy_webhook_ignores_branch_deletion():
    deploy, reason = webhook_deploy.should_deploy(
        "push",
        {"ref": "refs/heads/claude/epic-mccarthy-sl30hz", "deleted": True},
    )
    assert deploy is False


def test_deploy_webhook_deploys_matching_push():
    deploy, reason = webhook_deploy.should_deploy(
        "push", {"ref": "refs/heads/claude/epic-mccarthy-sl30hz"}
    )
    assert deploy is True


def test_deploy_webhook_service_runs_as_root_with_own_env_file():
    """
    Сознательно root (update.sh делает chown/systemctl), но НЕ делит
    окружение с nasluh.service -- секрет деплоя не должен быть виден
    процессу, который разбирает чужой ввод.
    """
    unit = (DEPLOY / "nasluh-deploy-webhook.service").read_text(encoding="utf-8")
    assert "User=root" in unit
    assert "EnvironmentFile=/opt/nasluh/deploy-webhook.env" in unit
    assert "nasluh.env" not in unit


def test_deploy_webhook_nginx_location_proxies_to_internal_port(nginx):
    assert "location /internal/deploy-webhook" in nginx
    assert "proxy_pass http://127.0.0.1:8099" in nginx


def test_install_deploy_webhook_generates_a_secret():
    text = (DEPLOY / "install_deploy_webhook.sh").read_text(encoding="utf-8")
    assert "openssl rand -hex 32" in text
    assert "nasluh-deploy-webhook.service" in text
    # Не должен перезаписывать уже существующий секрет при повторном запуске.
    assert "if [ ! -f \"$ENV_FILE\" ]" in text
