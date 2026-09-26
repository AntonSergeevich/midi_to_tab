"""Ретранслятор запросов к Mureka для NASLUX (RunPod Serverless, CPU).

С российского сервера сайта API Mureka отвечает 403 «Service unavailable»,
а из-за рубежа работает. Этот воркер ничего не генерирует -- он делает
запросы к Mureka за сайт и перекладывает файлы. Ключ Mureka живёт в
переменных окружения шаблона RunPod (MUREKA_API_KEY) и через сайт не ходит.

Операции (input.op):
  call   -- {method, path, body}: один JSON-запрос к API;
  upload -- {source_url, purpose, filename}: скачать исходник по ссылке
            сайта и загрузить в Mureka (files/upload);
  stem   -- {source_url, model}: разделение на партии (song/stem);
  fetch  -- {url, upload_url, name}: скачать файл Mureka и отдать сайту.
Ответ: {"ok", "status", "json" | "bytes" | "error"} -- статус и текст
ошибки Mureka сайт разбирает сам (очередь 429, деньги, прочее).
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

import runpod

API = "https://api.mureka.ai"
UA = "naslux-relay/1.0 (+https://naslux.ru)"


def _open(request, timeout=300):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _mureka(method, path, body=None, headers=None, timeout=300):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {os.environ['MUREKA_API_KEY']}", "User-Agent": UA,
        **({"Content-Type": "application/json"} if not isinstance(body, bytes) else {}),
        **(headers or {})})
    status, raw = _open(request, timeout)
    if status >= 400:
        return {"ok": False, "status": status, "error": raw[:3000].decode(errors="replace")}
    return {"ok": True, "status": status, "json": json.loads(raw or b"{}")}


def _get(url):
    status, raw = _open(urllib.request.Request(url, headers={"User-Agent": UA}))
    if status >= 400:
        raise RuntimeError(f"не скачать {url[:80]}: HTTP {status}")
    return raw


def handler(job):
    task = job.get("input") or {}
    op = task.get("op")
    try:
        if op == "call":
            return _mureka(task.get("method", "GET"), task["path"], task.get("body"))
        if op == "upload":
            content = _get(task["source_url"])
            boundary = uuid.uuid4().hex
            name = task.get("filename") or "track.mp3"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n"
                    f"{task['purpose']}\r\n--{boundary}\r\nContent-Disposition: form-data; "
                    f"name=\"file\"; filename=\"{name}\"\r\nContent-Type: audio/mpeg\r\n\r\n"
                    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
            return _mureka("POST", "/v1/files/upload", body, {
                "Content-Type": f"multipart/form-data; boundary={boundary}"})
        if op == "stem":
            data = base64.b64encode(_get(task["source_url"])).decode()
            return _mureka("POST", "/v1/song/stem", {
                "url": f"data:audio/mp3;base64,{data}",
                "model": task.get("model") or "audio-separation-2"}, timeout=900)
        if op == "fetch":
            content = _get(task["url"])
            request = urllib.request.Request(task["upload_url"], data=content, method="POST", headers={
                "Content-Type": "application/octet-stream", "User-Agent": UA,
                "X-File-Name": urllib.parse.quote(task["name"])})
            status, raw = _open(request)
            if status >= 400:
                return {"ok": False, "status": status, "error": raw[:500].decode(errors="replace")}
            return {"ok": True, "status": status, "bytes": len(content)}
        return {"ok": False, "status": 400, "error": f"неизвестная операция {op!r}"}
    except Exception as error:  # noqa: BLE001 -- ответ сайту вместо падения воркера
        return {"ok": False, "status": 599, "error": str(error)[:1000]}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
