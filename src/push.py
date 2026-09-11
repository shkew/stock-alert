import os
from urllib.parse import quote

import requests


def push_markdown(title: str, markdown: str) -> None:
    channels = [item.strip().lower() for item in os.getenv("PUSH_CHANNELS", "bark").split(",") if item.strip()]
    errors: list[str] = []

    if "bark" in channels:
        try:
            _push_bark(title, markdown)
        except Exception as exc:
            errors.append(f"Bark 推送失败：{exc}")

    if "serverchan" in channels:
        try:
            _push_serverchan(title, markdown)
        except Exception as exc:
            errors.append(f"Server 酱推送失败：{exc}")

    if "wecom" in channels:
        try:
            _push_wecom(markdown)
        except Exception as exc:
            errors.append(f"企业微信群机器人推送失败：{exc}")

    if errors:
        raise RuntimeError("; ".join(errors))


def _push_serverchan(title: str, markdown: str) -> None:
    sendkey = os.getenv("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        raise ValueError("SERVERCHAN_SENDKEY is empty.")

    if sendkey.startswith("sctp"):
        url = f"https://{sendkey}.push.ft07.com/send"
        payload = {"title": title, "desp": markdown}
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {"title": title, "desp": markdown}

    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()
    result = response.json()
    if int(result.get("code", 0)) not in (0, 200):
        raise RuntimeError(str(result))


def _push_wecom(markdown: str) -> None:
    webhook = os.getenv("WECOM_BOT_WEBHOOK", "").strip()
    if not webhook:
        raise ValueError("WECOM_BOT_WEBHOOK is empty.")

    response = requests.post(webhook, json={"msgtype": "markdown", "markdown": {"content": markdown}}, timeout=20)
    response.raise_for_status()
    result = response.json()
    if int(result.get("errcode", 0)) != 0:
        raise RuntimeError(str(result))


def _push_bark(title: str, markdown: str) -> None:
    endpoint = os.getenv("BARK_ENDPOINT", "").strip()
    key = os.getenv("BARK_KEY", "").strip()
    server = os.getenv("BARK_SERVER", "https://api.day.app").strip().rstrip("/")

    if endpoint:
        url = endpoint.rstrip("/")
    elif key:
        url = f"{server}/{key}"
    else:
        raise ValueError("BARK_ENDPOINT or BARK_KEY is empty.")

    body = _compact_body(markdown)
    payload = {
        "title": title,
        "body": body,
        "group": os.getenv("BARK_GROUP", "股票提醒"),
        "isArchive": "1",
    }

    level = os.getenv("BARK_LEVEL", "").strip()
    sound = os.getenv("BARK_SOUND", "").strip()
    url_link = os.getenv("BARK_URL", "").strip()
    if level:
        payload["level"] = level
    if sound:
        payload["sound"] = sound
    if url_link:
        payload["url"] = url_link

    response = requests.post(url, json=payload, timeout=20)
    if response.status_code in (404, 405):
        fallback = f"{url}/{quote(title)}/{quote(body[:1800])}"
        response = requests.get(fallback, timeout=20)
    response.raise_for_status()


def _compact_body(markdown: str, limit: int = 3500) -> str:
    lines = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.replace("#", "").replace("*", "")
        lines.append(line)
    body = "\n".join(lines)
    return body if len(body) <= limit else body[: limit - 20] + "\n...内容已截断"
