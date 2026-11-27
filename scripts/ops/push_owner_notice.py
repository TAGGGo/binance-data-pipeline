"""
Send an owner-only WeChat alert to Avalon via PushPlus (PUSHPLUS_TOKEN only).

Reads /Users/ivantang/Projects/datahub/binance-data-pipeline/.env silently if needed.
Never uses PUSHPLUS_READER_TOKEN (dad's token) or PUSHPLUS_TO, and never prints tokens.

Usage:
  python scripts/ops/push_owner_notice.py --title "Title" --body "Message body"
  python scripts/ops/push_owner_notice.py --title "Title" --body-file /path/to/body.txt
  echo "Message body" | python scripts/ops/push_owner_notice.py --title "Title"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
PUSHPLUS_URL = "https://www.pushplus.plus/send"


def _load_dotenv() -> None:
    if os.environ.get("PUSHPLUS_TOKEN"):
        return
    env_file = REPO / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        if k == "PUSHPLUS_TOKEN" and v:
            os.environ.setdefault(k, v)


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8").strip()
    if args.body is not None and args.body != "-":
        return args.body.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send an owner-only PushPlus WeChat notification to Avalon (PUSHPLUS_TOKEN only)."
    )
    parser.add_argument("--title", required=True, help="Notification title")
    parser.add_argument("--body", default=None, help="Notification body (or '-' to read from stdin)")
    parser.add_argument("--body-file", default=None, help="UTF-8 file containing notification body")
    parser.add_argument("--template", choices=("txt", "markdown"), default="txt", help="PushPlus template")
    args = parser.parse_args()

    _load_dotenv()
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        print("push_owner_notice: PUSHPLUS_TOKEN not configured", file=sys.stderr)
        return 1

    title = args.title.strip()
    content = _read_body(args)
    if not title or not content:
        print("push_owner_notice: both --title and non-empty body are required", file=sys.stderr)
        return 1

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": args.template,
    }
    try:
        resp = requests.post(PUSHPLUS_URL, json=payload, timeout=30)
        data = resp.json()
    except Exception as exc:
        print(f"push_owner_notice: request failed ({type(exc).__name__})", file=sys.stderr)
        return 1

    if data.get("code") != 200:
        msg = data.get("msg") or f"code={data.get('code')}"
        print(f"push_owner_notice: failed ({msg})", file=sys.stderr)
        return 1

    print(f"push_owner_notice: sent to Avalon ({title})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
