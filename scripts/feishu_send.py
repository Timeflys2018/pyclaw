#!/usr/bin/env python3
"""Send messages to a Feishu bot as a real user (OAuth) or as the bot itself (tenant token).

Usage:

  # Step 1: First-time OAuth — opens browser, saves session for reuse
  python3 scripts/feishu_send.py auth

  # Step 2: Send a P2P message (reuses saved session, auto-refreshes token)
  python3 scripts/feishu_send.py send "你好，测试一下记忆功能"

  # Send to a group chat (@ the bot)
  python3 scripts/feishu_send.py send "帮我看看 Redis 配置" --chat-id oc_xxx

  # Use bot identity instead of user identity (no OAuth needed, but won't trigger event handler)
  python3 scripts/feishu_send.py send-as-bot "你好" --receive-id ou_xxx

  # Query bot_open_id
  python3 scripts/feishu_send.py bot-info

Config is read from configs/pyclaw.json (channels.feishu section).
OAuth session is saved to artifacts/feishu-oauth-session.json for reuse.
"""

from __future__ import annotations

import argparse
import json
import secrets
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

FEISHU_BASE = "https://open.feishu.cn/open-apis"
AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = f"{FEISHU_BASE}/authen/v2/oauth/token"
SEND_URL = f"{FEISHU_BASE}/im/v1/messages"
BOT_INFO_URL = f"{FEISHU_BASE}/bot/v3/info"
TENANT_TOKEN_URL = f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal"

SESSION_FILE = Path("artifacts/feishu-oauth-session.json")
CONFIG_FILE = Path("configs/pyclaw.json")


def _load_feishu_config() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        sys.exit(f"Config not found: {CONFIG_FILE}")
    data = json.loads(CONFIG_FILE.read_text())
    feishu = data.get("channels", {}).get("feishu", {})
    app_id = feishu.get("appId", "")
    app_secret = feishu.get("appSecret", "")
    if not app_id or not app_secret:
        sys.exit("Missing channels.feishu.appId or appSecret in config")
    return {"app_id": app_id, "app_secret": app_secret}


def _http_json(
    url: str, headers: dict[str, str], body: bytes | None = None, method: str = "POST"
) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return {"code": -1, "msg": f"HTTP {e.code}: {detail}"}
    except urllib.error.URLError as e:
        return {"code": -1, "msg": str(e)}


def _get_tenant_token(app_id: str, app_secret: str) -> str:
    payload = _http_json(
        TENANT_TOKEN_URL,
        {"Content-Type": "application/json"},
        json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
    )
    if payload.get("code") != 0:
        sys.exit(f"Failed to get tenant token: {payload.get('msg')}")
    return payload["tenant_access_token"]


def _load_session() -> dict[str, Any]:
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())
    return {}


def _save_session(session: dict[str, Any]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2))


def _refresh_token(app_id: str, app_secret: str, refresh_token: str) -> dict[str, Any]:
    payload = _http_json(
        TOKEN_URL,
        {"Content-Type": "application/json"},
        json.dumps({
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "refresh_token": refresh_token,
        }).encode(),
    )
    if payload.get("code") != 0:
        return {"status": "error", "msg": payload.get("msg", "")}
    data = payload.get("data") or payload
    return {
        "status": "ok",
        "user_access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token", ""),
        "open_id": (data.get("user_info") or {}).get("open_id", data.get("open_id", "")),
    }


def _exchange_code(
    app_id: str, app_secret: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    payload = _http_json(
        TOKEN_URL,
        {"Content-Type": "application/json"},
        json.dumps({
            "grant_type": "authorization_code",
            "client_id": app_id,
            "client_secret": app_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }).encode(),
    )
    if payload.get("code") != 0:
        return {"status": "error", "msg": payload.get("msg", "")}
    data = payload.get("data") or payload
    return {
        "status": "ok",
        "user_access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token", ""),
        "open_id": (data.get("user_info") or {}).get("open_id", data.get("open_id", "")),
    }


def _get_user_token(config: dict[str, str]) -> str:
    session = _load_session()
    if session.get("refresh_token"):
        result = _refresh_token(config["app_id"], config["app_secret"], session["refresh_token"])
        if result["status"] == "ok":
            session["user_access_token"] = result["user_access_token"]
            session["refresh_token"] = result["refresh_token"]
            session["open_id"] = result.get("open_id", session.get("open_id", ""))
            _save_session(session)
            return session["user_access_token"]
        print(f"Token refresh failed: {result['msg']}, re-auth needed", file=sys.stderr)

    sys.exit(
        "No valid OAuth session. Run 'python3 scripts/feishu_send.py auth' first."
    )


def _send_message(
    token: str,
    receive_id: str,
    text: str,
    receive_id_type: str = "open_id",
    mention_open_id: str = "",
    mention_name: str = "",
) -> dict[str, Any]:
    content_text = text
    if receive_id_type == "chat_id" and mention_open_id:
        label = mention_name or mention_open_id
        content_text = f'<at user_id="{mention_open_id}">{label}</at> {text}'

    url = f"{SEND_URL}?receive_id_type={receive_id_type}"
    body = json.dumps({
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": content_text}, ensure_ascii=False),
    }, ensure_ascii=False).encode()

    payload = _http_json(url, {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }, body)

    if payload.get("code") == 0:
        msg_id = (payload.get("data") or {}).get("message_id", "")
        return {"status": "ok", "message_id": msg_id}
    return {"status": "error", "msg": payload.get("msg", "")}


def cmd_auth(args: argparse.Namespace) -> int:
    config = _load_feishu_config()
    redirect_uri = f"http://{args.host}:{args.port}/callback"
    state = secrets.token_urlsafe(16)
    scopes = ["im:message", "im:message.send_as_user", "offline_access"]

    auth_url = (
        f"{AUTHORIZE_URL}?"
        + urllib.parse.urlencode({
            "client_id": config["app_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        })
    )

    print(f"\nOpen this URL in your browser:\n\n  {auth_url}\n")
    print(f"Waiting for OAuth callback on {redirect_uri} ...")

    result: dict[str, str] = {}
    event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            result["code"] = (qs.get("code") or [""])[0]
            result["state"] = (qs.get("state") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Authorization received. You can close this tab.\n".encode())
            event.set()

        def log_message(self, format: str, *_args: Any) -> None:
            pass

    server = HTTPServer((args.host, args.port), Handler)
    server.timeout = 300
    while not event.is_set():
        server.handle_request()
        if not result.get("code"):
            break
    server.server_close()

    if not result.get("code"):
        sys.exit("OAuth callback timed out or missing code.")
    if result.get("state") != state:
        sys.exit(f"OAuth state mismatch: expected {state}, got {result.get('state')}")

    token_result = _exchange_code(
        config["app_id"], config["app_secret"], result["code"], redirect_uri
    )
    if token_result["status"] != "ok":
        sys.exit(f"Token exchange failed: {token_result['msg']}")

    _save_session({
        "app_id": config["app_id"],
        "open_id": token_result["open_id"],
        "user_access_token": token_result["user_access_token"],
        "refresh_token": token_result["refresh_token"],
    })
    print(f"\n✅ Authorized as open_id={token_result['open_id']}")
    print(f"   Session saved to {SESSION_FILE}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    config = _load_feishu_config()
    token = _get_user_token(config)

    if args.chat_id:
        bot_open_id = _get_bot_open_id(config)
        result = _send_message(
            token, args.chat_id, args.message,
            receive_id_type="chat_id",
            mention_open_id=bot_open_id,
            mention_name="PyClaw",
        )
    else:
        bot_open_id = _get_bot_open_id(config)
        result = _send_message(token, bot_open_id, args.message)

    if result["status"] == "ok":
        print(f"✅ Sent. message_id={result['message_id']}")
        return 0
    print(f"❌ Failed: {result['msg']}", file=sys.stderr)
    return 1


def cmd_send_as_bot(args: argparse.Namespace) -> int:
    config = _load_feishu_config()
    token = _get_tenant_token(config["app_id"], config["app_secret"])
    result = _send_message(token, args.receive_id, args.message)
    if result["status"] == "ok":
        print(f"✅ Sent as bot. message_id={result['message_id']}")
        return 0
    print(f"❌ Failed: {result['msg']}", file=sys.stderr)
    return 1


def _get_bot_open_id(config: dict[str, str]) -> str:
    token = _get_tenant_token(config["app_id"], config["app_secret"])
    payload = _http_json(
        BOT_INFO_URL,
        {"Authorization": f"Bearer {token}"},
        method="GET",
        body=None,
    )
    bot = (payload.get("bot") or {})
    bot_open_id = bot.get("open_id", "")
    if not bot_open_id:
        sys.exit(f"Failed to get bot_open_id: {payload}")
    return bot_open_id


def cmd_bot_info(args: argparse.Namespace) -> int:
    config = _load_feishu_config()
    bot_open_id = _get_bot_open_id(config)
    print(f"bot_open_id: {bot_open_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PyClaw Feishu message sender")
    sub = parser.add_subparsers(dest="command")

    p_auth = sub.add_parser("auth", help="OAuth authorize (first-time setup)")
    p_auth.add_argument("--host", default="localhost")
    p_auth.add_argument("--port", type=int, default=3000)

    p_send = sub.add_parser("send", help="Send P2P or group message as user")
    p_send.add_argument("message", nargs="?", default="你好")
    p_send.add_argument("--chat-id", default="", help="Send to group chat instead of P2P")

    p_bot = sub.add_parser("send-as-bot", help="Send message as bot (tenant token)")
    p_bot.add_argument("message", nargs="?", default="你好")
    p_bot.add_argument("--receive-id", required=True, help="Target open_id")

    sub.add_parser("bot-info", help="Query bot_open_id")

    args = parser.parse_args()
    if args.command == "auth":
        return cmd_auth(args)
    if args.command == "send":
        return cmd_send(args)
    if args.command == "send-as-bot":
        return cmd_send_as_bot(args)
    if args.command == "bot-info":
        return cmd_bot_info(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
