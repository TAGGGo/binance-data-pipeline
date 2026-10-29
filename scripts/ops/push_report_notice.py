"""
WeChat (PushPlus) one-line notice to dad when 币聊's 今日日报 has been updated:「日报已经更新，请去『币聊』查看」.

Sends once per new report.json ("at" changed), only between 07:00 and 22:00 Beijing, only if the report is < 20 h old.
Called right after the morning scheduled task writes report.json, and hourly from run_update.sh as a fallback
(whichever comes first; the other one sees it was already sent).
  python3 scripts/ops/push_report_notice.py            send if due
  python3 scripts/ops/push_report_notice.py --to-me    test: send to Avalon's own WeChat, state untouched
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "data" / "state" / "crypto-chat" / "report.json"
STATE = REPO / "data" / "state" / "crypto-chat-notice.txt"
TEXT = "日报已经更新，请去『币聊』查看"


def main() -> int:
    me = "--to-me" in sys.argv
    try:
        at = json.loads(REPORT.read_text()).get("at") or ""
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except Exception as e:
        print(f"push_report_notice: no report ({e})")
        return 0
    now = datetime.now(timezone.utc)
    bj = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if not me:
        if STATE.exists() and STATE.read_text().strip() == at:
            print("push_report_notice: already sent for", at)
            return 0
        if now - t > timedelta(hours=20):
            print("push_report_notice: report too old", at)
            return 0
        if not 7 <= bj.hour < 22:
            print("push_report_notice: night in Beijing, waiting")
            return 0
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip() if me else (os.environ.get("PUSHPLUS_READER_TOKEN", "").strip() or os.environ.get("PUSHPLUS_TOKEN", "").strip())
    if not token:
        print("push_report_notice: no PushPlus token")
        return 1
    body = {"token": token, "title": TEXT, "content": TEXT, "template": "txt"}
    j = requests.post("https://www.pushplus.plus/send", json=body, timeout=30).json()
    if j.get("code") != 200:
        print("push_report_notice: failed", j)
        return 1
    if not me:
        STATE.write_text(at)
    print(f"push_report_notice: sent {'(test, to Avalon)' if me else 'to dad'} for report {at}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
