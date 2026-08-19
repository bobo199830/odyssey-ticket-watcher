#!/usr/bin/env python3
"""Conservative Odyssey availability watcher.

Exit non-zero on fetch/parse failures so a broken source cannot overwrite good state.
Never logs the ServerChan SendKey.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

SOURCE_URL = os.getenv("TICKET_SOURCE_URL", "https://www.maoyan.com/cinema/181")
STATE_FILE = Path(os.getenv("STATE_FILE", ".state/odyssey.json"))
TITLE_WORDS = ("奥德赛", "The Odyssey")
AVAILABLE_WORDS = ("选座购票", "立即购票", "购票")
UNAVAILABLE_WORDS = ("座位已满", "售罄", "暂无场次", "不可售")
UA = "Mozilla/5.0 (compatible; OdysseyTicketWatcher/1.0; +https://github.com/bobo199830/odyssey-ticket-watcher)"


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urlopen(req, timeout=25) as resp:
        if resp.status != 200:
            raise RuntimeError(f"source returned HTTP {resp.status}")
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def visible_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<(?:br|/p|/div|/li|/tr|/section|/h[1-6])[^>]*>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = html.unescape(raw).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


def movie_section(text: str) -> str:
    positions = [text.find(w) for w in TITLE_WORDS if text.find(w) >= 0]
    if not positions:
        raise RuntimeError("target movie title was not found; refusing to change state")
    start = min(positions)
    # Maoyan lists each movie as a compact block. Keep a bounded window so other
    # films' sessions cannot create false positives.
    return text[start:start + 5000]


def parse_sessions(text: str) -> dict[str, dict]:
    section = movie_section(text)
    sessions: dict[str, dict] = {}
    # Dates and showtimes occur in reading order on the cinema page.
    token_re = re.compile(
        r"(?P<date>(?:今天|明天|后天|周[一二三四五六日天])?\s*\d{1,2}月\d{1,2}(?:日|号)?)"
        r"|(?P<time>(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d))"
    )
    current_date = "日期待确认"
    for match in token_re.finditer(section):
        if match.group("date"):
            current_date = re.sub(r"\s+", "", match.group("date"))
            continue
        showtime = match.group("time")
        # Ignore a likely end-time when immediately preceded by “散场”.
        before = section[max(0, match.start() - 8):match.start()]
        after = section[match.end():match.end() + 8]
        if "散场" in before or "散场" in after:
            continue
        context = section[match.start():match.start() + 220]
        markers = []
        markers.extend((context.find(w), False) for w in UNAVAILABLE_WORDS if w in context)
        markers.extend((context.find(w), True) for w in AVAILABLE_WORDS if w in context)
        # Use the first explicit sale-status marker in this row. This prevents a
        # following row's status from overriding the current session.
        available = min(markers, default=(sys.maxsize, False))[1]
        # A session is recorded even when sold out, but ambiguous markup is not
        # called available. This makes notifications deliberately conservative.
        status = "AVAILABLE" if available else "UNAVAILABLE"
        hall_match = re.search(r"([\w\u4e00-\u9fff +.-]{0,30}(?:IMAX|巨幕)[\w\u4e00-\u9fff +.-]{0,30}(?:厅)?)", context, re.I)
        hall = hall_match.group(1).strip() if hall_match else ""
        key = hashlib.sha256(f"{current_date}|{showtime}|{hall}".encode()).hexdigest()[:16]
        sessions[key] = {
            "date": current_date, "time": showtime, "hall": hall,
            "status": status, "source": SOURCE_URL,
        }
    if not sessions:
        raise RuntimeError("movie was found but no sessions could be parsed; refusing to change state")
    return sessions


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("sessions", {})
    except (OSError, ValueError, TypeError):
        raise RuntimeError("cached state is invalid; refusing to treat this as a first run")


def changes(previous: dict, current: dict) -> list[dict]:
    result = []
    for key, item in current.items():
        if item["status"] != "AVAILABLE":
            continue
        old = previous.get(key)
        if old is None:
            result.append({"event": "NEW_AVAILABLE", **item})
        elif old.get("status") != "AVAILABLE":
            result.append({"event": "RESTOCK", **item})
    return result


def notify(items: list[dict]) -> None:
    sendkey = os.getenv("SERVERCHAN_SENDKEY", "")
    if not sendkey:
        raise RuntimeError("SERVERCHAN_SENDKEY is not configured")
    title = f"《奥德赛》有票变化：{len(items)} 个场次"
    lines = [f"- {x['event']}｜{x['date']} {x['time']} {x['hall']}".rstrip() for x in items]
    body = "\n".join(lines) + f"\n\n购票页面：{SOURCE_URL}"
    url = f"https://sctapi.ftqq.com/{quote(sendkey, safe='')}.send"
    payload = f"title={quote(title)}&desp={quote(body)}".encode()
    req = Request(url, data=payload, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=20) as resp:
        response = json.loads(resp.read().decode("utf-8"))
    if response.get("code") not in (0, "0"):
        raise RuntimeError("ServerChan rejected the notification")


def test_notification() -> None:
    notify([{"event": "MANUAL_TEST", "date": datetime.now().astimezone().strftime("%Y-%m-%d"),
             "time": datetime.now().astimezone().strftime("%H:%M"), "hall": "（非票务变化）"}])


def main() -> int:
    if os.getenv("SEND_TEST_NOTIFICATION", "").lower() == "true":
        test_notification()
        print("Manual test notification sent.")
        return 0

    current = parse_sessions(visible_text(fetch(SOURCE_URL)))
    first_run = not STATE_FILE.exists()
    previous = load_state()
    found = [] if first_run else changes(previous, current)
    print(f"Parsed {len(current)} target sessions; detected {len(found)} real changes.")
    if found:
        notify(found)
        print("Change notification sent.")
    elif first_run:
        print("Baseline initialized; no notification sent.")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "updated_at": datetime.now().astimezone().isoformat(),
        "source": SOURCE_URL, "sessions": current,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
