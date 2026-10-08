#!/usr/bin/env bash
# Сторож сайта: раз в минуту (nasluh-watchdog.timer), от root.
#
# 08.10 приложение зависло часов на девять: nginx отдавал статику за 0.6 с,
# а сам сайт молчал, пока его не перезапустила выкладка. Не упало --
# Restart=always не помог. Здесь: две проверки подряд без ответа --
# снимок «чем были заняты потоки» и память службы в data/hangs/, затем
# перезапуск. Снимок виден в /api/health (проверка с GitHub).
set -uo pipefail

DATA=/opt/nasluh/data
STATE="$DATA/watchdog.fails"
URL=http://127.0.0.1:8000/robots.txt

if curl -fsS -o /dev/null --max-time 20 "$URL"; then
    echo 0 > "$STATE"
    exit 0
fi

# Только что перезапущена (выкладка, сбой) -- ещё грузит модели, не трогаем
since=$(systemctl show nasluh -p ActiveEnterTimestamp --value)
if [ -n "$since" ] && [ $(( $(date +%s) - $(date -d "$since" +%s 2>/dev/null || echo 0) )) -lt 180 ]; then
    exit 0
fi

fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
[ "$fails" -lt 2 ] && exit 0

mkdir -p "$DATA/hangs"
out="$DATA/hangs/$(date -u +%Y%m%dT%H%M%SZ).log"
cg=/sys/fs/cgroup/system.slice/nasluh.service
{
    echo "сайт не ответил дважды за $URL"
    echo "память службы: $(cat $cg/memory.current 2>/dev/null) байт, предел $(cat $cg/memory.max 2>/dev/null)"
    echo "события памяти: $(tr '\n' ' ' < $cg/memory.events 2>/dev/null)"
    echo "давление памяти: $(head -1 $cg/memory.pressure 2>/dev/null)"
    echo "процессор: $(head -1 $cg/cpu.pressure 2>/dev/null)"
    echo "свободно на сервере: $(free -m | awk '/Mem:/{print $7" МБ из "$2}')"
    # faulthandler в приложении (SIGUSR1) печатает стеки всех потоков в журнал.
    # Берём только строки стеков: в журнале бывают и адреса запросов.
    systemctl kill -s SIGUSR1 --kill-who=main nasluh 2>/dev/null
    sleep 3
    journalctl -u nasluh --since "-20s" --no-pager -o cat 2>/dev/null \
        | grep -E '^(Thread 0x|Current thread 0x|  File ")' | tail -120
} > "$out" 2>&1
chown -R nasluh:nasluh "$DATA/hangs"

systemctl restart nasluh
echo 0 > "$STATE"
