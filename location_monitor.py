#!/usr/bin/env python3
"""
Location Auto Messenger — Python Backend
특정 장소 도착 시 자동 메시지 발송 서버

역할:
  1. location_messenger.html 에서 /api/send-email, /api/send-sms 요청을 처리
  2. 독립 모드: GPS 좌표를 stdin/파일/HTTP API로 받아 도착 여부 직접 판단
  3. SMS/이메일/Webhook/카카오 발송을 통합 처리

Usage:
  # 웹 API 서버 (브라우저와 연동)
  python location_monitor.py serve --port 8080

  # 독립 CLI 모드 (GPS 장치/앱에서 좌표를 파이프로 전달)
  echo '37.5665,126.9780' | python location_monitor.py monitor

  # 장소 목록 출력
  python location_monitor.py list

  # 테스트 도착 이벤트 발동
  python location_monitor.py test-arrive --target-id 1
"""

import argparse
import json
import logging
import math
import smtplib
import sys
import time
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
TARGETS_FILE = BASE_DIR / "location_targets.json"


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def load_targets() -> list[dict]:
    if TARGETS_FILE.exists():
        return json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    # Default demo targets
    return [
        {
            "id": 1,
            "name": "서울 시청 (예시)",
            "lat": 37.5665,
            "lng": 126.9780,
            "radius": 150,
            "message": "[도착 알림] {장소이름}에 도착했습니다. 현재 시각: {시간}",
            "send_method": "email",
            "email_to": "",
            "phone": "",
            "sms_provider": "coolsms",
            "webhook_url": "",
            "kakao_channel": "",
        }
    ]


def save_targets(targets: list[dict]) -> None:
    TARGETS_FILE.write_text(json.dumps(targets, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# GEO UTILS
# ─────────────────────────────────────────────────────────────────────────────

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return distance in meters between two GPS coordinates."""
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ/2)**2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_message(template: str, target: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (template
            .replace("{장소이름}", target["name"])
            .replace("{시간}", now)
            .replace("{반경}", str(target["radius"]) + "m"))


# ─────────────────────────────────────────────────────────────────────────────
# SENDERS
# ─────────────────────────────────────────────────────────────────────────────

class EmailSender:
    def __init__(self, cfg: dict):
        email_cfg = cfg.get("email", {})
        self.host      = email_cfg.get("smtp_host", "smtp.gmail.com")
        self.port      = email_cfg.get("smtp_port", 587)
        self.user      = email_cfg.get("smtp_user", "")
        self.password  = email_cfg.get("smtp_password", "")
        self.from_addr = email_cfg.get("from_address", self.user)
        self.from_name = email_cfg.get("from_name", "LocationMessenger")

    def send(self, to: str, subject: str, body: str) -> bool:
        if not self.user or not self.password:
            log.warning("SMTP 설정이 없습니다. config.json의 email 섹션을 확인하세요.")
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{self.from_name} <{self.from_addr}>"
            msg["To"]      = to
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.host, self.port) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.from_addr, to, msg.as_string())

            log.info(f"✓ 이메일 발송 완료 → {to}")
            return True
        except Exception as e:
            log.error(f"✗ 이메일 발송 실패: {e}")
            return False


class WebhookSender:
    def send(self, url: str, message: str) -> bool:
        if not url:
            return False
        payload = json.dumps({"text": message, "username": "LocationMessenger"}).encode()
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status < 300
            log.info(f"{'✓' if ok else '✗'} Webhook {'발송 완료' if ok else '발송 실패'} → {url[:50]}")
            return ok
        except Exception as e:
            log.error(f"Webhook 오류: {e}")
            return False


class SMSSender:
    """
    국내 SMS: CoolSMS(Solapi) API 연동 예시.
    실제 사용 시 해당 서비스의 SDK 또는 REST API 키를 config.json에 설정하세요.
    """
    def __init__(self, cfg: dict):
        loc_cfg = cfg.get("location_messenger", {})
        self.provider = loc_cfg.get("sms_provider", "coolsms")
        self.api_key  = loc_cfg.get("sms_api_key", "")
        self.from_num = loc_cfg.get("sms_from_number", "")

    def send(self, to: str, message: str) -> bool:
        if not self.api_key:
            log.warning(f"SMS API 키가 없습니다 ({self.provider}). config.json의 location_messenger.sms_api_key를 설정하세요.")
            return False
        # CoolSMS REST API example (실제 서명 로직은 Solapi SDK 참조)
        payload = {
            "message": {
                "to":   to,
                "from": self.from_num,
                "text": message,
            }
        }
        log.info(f"SMS 발송 시도 → {to} ({self.provider}) : {message[:40]}")
        # 실제 구현: import solapi; solapi.send(...)
        return True


class MessageDispatcher:
    def __init__(self, cfg: dict):
        self.email   = EmailSender(cfg)
        self.webhook = WebhookSender()
        self.sms     = SMSSender(cfg)

    def dispatch(self, target: dict, message: str) -> bool:
        method = target.get("send_method", "email")
        subject = f"[LocationMessenger] 도착 알림: {target['name']}"

        if method == "email":
            return self.email.send(target.get("email_to", ""), subject, message)
        elif method == "webhook":
            return self.webhook.send(target.get("webhook_url", ""), message)
        elif method == "sms":
            return self.sms.send(target.get("phone", ""), message)
        elif method == "kakao":
            log.info(f"카카오 발송 (구현 필요): {message[:60]}")
            return False
        return False


# ─────────────────────────────────────────────────────────────────────────────
# ARRIVAL MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class ArrivalMonitor:
    """
    Continuously checks GPS position against registered targets.
    Reads coordinates from stdin (one 'lat,lng' line per poll cycle) or
    from a shared file updated by a GPS app / mobile companion.
    """

    def __init__(self, dispatcher: MessageDispatcher, targets: list[dict],
                 interval: int = 15, once_per_target: bool = True):
        self.dispatcher = dispatcher
        self.targets    = targets
        self.interval   = interval
        self.once_per   = once_per_target
        self.sent_ids: set = set()

    def check(self, lat: float, lng: float) -> list[str]:
        """Check all targets. Return list of arrived target names."""
        arrived = []
        for t in self.targets:
            dist = haversine(lat, lng, t["lat"], t["lng"])
            if dist <= t["radius"]:
                if self.once_per and t["id"] in self.sent_ids:
                    continue
                self.sent_ids.add(t["id"])
                msg = build_message(t["message"], t)
                log.info(f"🎉 도착 감지: '{t['name']}' (거리: {dist:.0f}m)")
                self.dispatcher.dispatch(t, msg)
                arrived.append(t["name"])
        return arrived

    def run_from_stdin(self):
        """Read 'lat,lng' from stdin line-by-line (piped from GPS tool)."""
        log.info("stdin에서 GPS 좌표를 읽습니다. 형식: lat,lng (예: 37.5665,126.9780)")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                lat, lng = map(float, line.split(","))
                arrived = self.check(lat, lng)
                if arrived:
                    log.info(f"발송 완료: {', '.join(arrived)}")
            except ValueError:
                log.warning(f"잘못된 형식: '{line}' (예: 37.5665,126.9780)")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP API SERVER
# ─────────────────────────────────────────────────────────────────────────────

def make_handler(dispatcher: MessageDispatcher, targets: list[dict], monitor: ArrivalMonitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.info(fmt % args)

        def send_json(self, code: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if self.path == "/api/targets":
                self.send_json(200, {"targets": targets})
            elif self.path == "/api/status":
                self.send_json(200, {"status": "ok", "targets": len(targets)})
            else:
                self.send_json(404, {"error": "Not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b"{}")

            if self.path == "/api/update-position":
                # Called by browser every N seconds with current GPS position
                lat = body.get("lat")
                lng = body.get("lng")
                if lat is None or lng is None:
                    self.send_json(400, {"error": "lat and lng required"})
                    return
                arrived = monitor.check(float(lat), float(lng))
                self.send_json(200, {"ok": True, "arrived": arrived})

            elif self.path == "/api/send-email":
                ok = dispatcher.email.send(
                    body.get("to", ""),
                    body.get("subject", "[LocationMessenger] 도착 알림"),
                    body.get("body", ""),
                )
                self.send_json(200 if ok else 500, {"ok": ok})

            elif self.path == "/api/send-sms":
                ok = dispatcher.sms.send(body.get("phone", ""), body.get("message", ""))
                self.send_json(200 if ok else 500, {"ok": ok})

            elif self.path == "/api/send-webhook":
                ok = dispatcher.webhook.send(body.get("url", ""), body.get("message", ""))
                self.send_json(200 if ok else 500, {"ok": ok})

            elif self.path == "/api/add-target":
                # Add new target from location_messenger.html form
                t = body
                t.setdefault("id", int(time.time() * 1000))
                targets.append(t)
                save_targets(targets)
                self.send_json(200, {"ok": True, "id": t["id"]})

            elif self.path == "/api/remove-target":
                tid = body.get("id")
                before = len(targets)
                targets[:] = [t for t in targets if t["id"] != tid]
                save_targets(targets)
                self.send_json(200, {"ok": True, "removed": before - len(targets)})

            else:
                self.send_json(404, {"error": "Unknown endpoint"})

    return Handler


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_serve(args):
    cfg        = load_config()
    targets    = load_targets()
    dispatcher = MessageDispatcher(cfg)
    monitor    = ArrivalMonitor(dispatcher, targets, interval=15)
    handler    = make_handler(dispatcher, targets, monitor)

    server = HTTPServer(("0.0.0.0", args.port), handler)
    log.info(f"Location Messenger API 서버 시작: http://0.0.0.0:{args.port}")
    log.info("엔드포인트: GET /api/targets  POST /api/update-position  POST /api/send-email")
    log.info("location_messenger.html 을 브라우저에서 열고 이 서버와 연동하세요.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("서버 종료.")


def cmd_monitor(_args):
    cfg        = load_config()
    targets    = load_targets()
    dispatcher = MessageDispatcher(cfg)
    monitor    = ArrivalMonitor(dispatcher, targets)
    monitor.run_from_stdin()


def cmd_list(_args):
    targets = load_targets()
    if not targets:
        print("등록된 장소가 없습니다.")
        return
    for t in targets:
        method_info = {"email": t.get("email_to"), "sms": t.get("phone"),
                       "webhook": t.get("webhook_url"), "kakao": t.get("kakao_channel")}.get(t.get("send_method",""), "—")
        print(f"[{t['id']}] {t['name']} — ({t['lat']:.5f}, {t['lng']:.5f}) 반경:{t['radius']}m "
              f"발송:{t.get('send_method','email')}→{method_info}")


def cmd_test_arrive(args):
    cfg        = load_config()
    targets    = load_targets()
    dispatcher = MessageDispatcher(cfg)
    target = next((t for t in targets if t["id"] == args.target_id), None)
    if not target:
        sys.exit(f"Target ID {args.target_id} 를 찾을 수 없습니다. `list` 명령으로 확인하세요.")
    msg = build_message(target["message"], target)
    log.info(f"테스트 도착 이벤트 발동: '{target['name']}'")
    log.info(f"메시지: {msg}")
    ok = dispatcher.dispatch(target, msg)
    log.info("발송 결과: " + ("성공 ✓" if ok else "실패 ✗ (SMTP 설정 확인)"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Location Auto Messenger — Backend")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="HTTP API 서버 시작 (브라우저 연동)")
    p_serve.add_argument("--port", type=int, default=8080)
    p_serve.set_defaults(func=cmd_serve)

    p_mon = sub.add_parser("monitor", help="stdin에서 GPS 좌표를 읽어 모니터링")
    p_mon.set_defaults(func=cmd_monitor)

    p_list = sub.add_parser("list", help="등록된 장소 목록 출력")
    p_list.set_defaults(func=cmd_list)

    p_test = sub.add_parser("test-arrive", help="특정 장소 도착 이벤트 테스트")
    p_test.add_argument("--target-id", type=int, required=True)
    p_test.set_defaults(func=cmd_test_arrive)

    args = parser.parse_args()
    args.func(args)
