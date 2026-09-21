"""
Проверки комплекта развёртывания.

Эти файлы нельзя прогнать целиком без сервера, но самое хрупкое в них --
согласованность путей и настроек между юнитом, установщиком и nginx.
Расхождение здесь стоит дорого: оно вылезает только в бою.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


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
    import subprocess
    import sys

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
    import subprocess
    import sys

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
    import subprocess
    import sys

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
