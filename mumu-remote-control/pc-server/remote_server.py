from __future__ import annotations

import base64
import json
import os
import random
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
GAME_PACKAGE = "com.xxzhz.tlad.ru"
SETTINGS_FILE = APP_DIR / "remote_server_settings.json"
PAIR_FILE = APP_DIR / "REMOTE_PAIRING.txt"
PAUSE_FILE = APP_DIR / "remote_pause_state.json"
BOT_RUNTIME_FILE = APP_DIR / "bot_runtime.json"
BOT_STATE_FILE = APP_DIR / "bot_state.json"
GUI_SETTINGS_FILE = APP_DIR / "gui_settings.json"
ACCOUNT_HISTORY_FILE = APP_DIR / "account_history.jsonl"
RESOURCE_HISTORY_FILE = APP_DIR / "resource_history.jsonl"

_SERVER = None
_SERVER_THREAD = None
_PAIR_CODE = None
_LOCK = threading.RLock()


def _atomic_write_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _settings():
    data = _read_json(SETTINGS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    if not data.get("token"):
        data["token"] = secrets.token_urlsafe(32)
    data.setdefault("host", "0.0.0.0")
    data.setdefault("port", 8765)
    data.setdefault("adb_path", "")
    _atomic_write_json(SETTINGS_FILE, data)
    return data


def _find_adb():
    cfg = _settings()
    if cfg.get("adb_path"):
        p = Path(str(cfg["adb_path"]))
        if p.exists():
            return str(p)
    candidates = [
        "adb",
        r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer\shell\adb.exe",
        r"C:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\nx_main\adb.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\nx_main\adb.exe",
    ]
    for item in candidates:
        if item == "adb":
            try:
                subprocess.run(["adb", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                return "adb"
            except Exception:
                continue
        if Path(item).exists():
            return item
    raise RuntimeError("ADB не найден. Укажи adb_path в remote_server_settings.json")


def _adb(*args, timeout=12, binary=False):
    cmd = [_find_adb(), *args]
    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(err or "ADB command failed")
    return cp.stdout if binary else cp.stdout.decode("utf-8", "ignore")


def _devices():
    runtime = _read_json(BOT_RUNTIME_FILE, {})
    runtime_devices = runtime.get("active_devices") if isinstance(runtime, dict) else None
    if isinstance(runtime_devices, list) and runtime_devices:
        return [str(x) for x in runtime_devices]
    out = _adb("devices")
    result = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            result.append(parts[0])
    return result


def _gui_name_map():
    data = _read_json(GUI_SETTINGS_FILE, {})
    result = {}
    if not isinstance(data, dict):
        return result
    for key in ("account_names", "device_names", "names"):
        value = data.get(key)
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, str) and v.strip():
                    result[str(k)] = v.strip()
    accounts = data.get("accounts")
    if isinstance(accounts, dict):
        for k, v in accounts.items():
            if isinstance(v, dict):
                name = v.get("name") or v.get("label")
                if isinstance(name, str) and name.strip():
                    result[str(k)] = name.strip()
    return result


def _pause_state():
    data = _read_json(PAUSE_FILE, {"devices": {}})
    if not isinstance(data, dict):
        data = {"devices": {}}
    if not isinstance(data.get("devices"), dict):
        data["devices"] = {}
    return data


def _set_pause(device, minutes):
    minutes = max(1, min(int(minutes), 1440))
    with _LOCK:
        data = _pause_state()
        data["devices"][device] = {
            "paused_until": time.time() + minutes * 60,
            "requested_at": time.time(),
            "minutes": minutes,
            "resume_requested": False,
            "game_stopped": False,
        }
        _atomic_write_json(PAUSE_FILE, data)
    return minutes


def _request_resume(device):
    with _LOCK:
        data = _pause_state()
        entry = data["devices"].setdefault(device, {})
        entry["paused_until"] = 0
        entry["resume_requested"] = True
        entry["requested_at"] = time.time()
        _atomic_write_json(PAUSE_FILE, data)


def _entry(device):
    return _pause_state().get("devices", {}).get(device)


def _force_stop_game(device):
    _adb("-s", device, "shell", "am", "force-stop", GAME_PACKAGE)


def _start_game(device):
    try:
        _adb("-s", device, "shell", "monkey", "-p", GAME_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1", timeout=15)
    except Exception:
        _adb("-s", device, "shell", "cmd", "package", "resolve-activity", "--brief", GAME_PACKAGE, timeout=10)


def remote_pause_gate(device: str) -> bool:
    """
    Вызывать в начале run_device_due_actions().
    True = этому устройству сейчас нельзя выполнять задачи.
    Текущая задача не обрывается: gate срабатывает после выхода из DeviceActionLock.
    """
    with _LOCK:
        data = _pause_state()
        entry = data["devices"].get(device)
        if not isinstance(entry, dict):
            return False

        now = time.time()
        paused_until = float(entry.get("paused_until") or 0)
        resume_requested = bool(entry.get("resume_requested"))

        if paused_until > now and not resume_requested:
            if not entry.get("game_stopped"):
                try:
                    _force_stop_game(device)
                    entry["game_stopped"] = True
                    entry["stopped_at"] = now
                    _atomic_write_json(PAUSE_FILE, data)
                    print(f"[Remote] {device}: игра остановлена до {datetime.fromtimestamp(paused_until).strftime('%H:%M:%S')}")
                except Exception as exc:
                    print(f"[Remote] {device}: не удалось закрыть игру: {exc}")
            return True

        try:
            _start_game(device)
            print(f"[Remote] {device}: пауза закончилась, Epic War запущена.")
        except Exception as exc:
            print(f"[Remote] {device}: не удалось запустить игру после паузы: {exc}")

        data["devices"].pop(device, None)
        _atomic_write_json(PAUSE_FILE, data)
        return True


def _runtime_device(device):
    runtime = _read_json(BOT_RUNTIME_FILE, {})
    devices = runtime.get("devices", {}) if isinstance(runtime, dict) else {}
    return devices.get(device, {}) if isinstance(devices, dict) else {}


def _device_state(device):
    state = _read_json(BOT_STATE_FILE, {})
    devices = state.get("devices", {}) if isinstance(state, dict) else {}
    return devices.get(device, {}) if isinstance(devices, dict) else {}


def _active_task(device):
    d = _device_state(device)
    if not isinstance(d, dict):
        return ""
    now = time.time()
    best = ""
    for key, value in d.items():
        if not isinstance(value, dict):
            continue
        started = value.get("started_at")
        if isinstance(started, (int, float)) and 0 <= now - float(started) < 3600:
            return str(key)
        if value.get("running") is True or value.get("in_progress") is True:
            best = str(key)
    return best


def _account_payload(device, index):
    runtime = _runtime_device(device)
    pause = _entry(device)
    paused_until = float(pause.get("paused_until") or 0) if isinstance(pause, dict) else 0
    remaining = max(0, int(paused_until - time.time()))
    names = _gui_name_map()
    return {
        "device": device,
        "name": names.get(device) or f"Аккаунт {index + 1}",
        "paused": remaining > 0,
        "pause_remaining_seconds": remaining,
        "active_task": _active_task(device),
        "foreground_app": runtime.get("foreground_app") if isinstance(runtime, dict) else None,
        "resources": {
            "battle_power_text": runtime.get("battle_power_text"),
            "gold_text": runtime.get("gold_text"),
            "food_text": runtime.get("food_text"),
            "wood_text": runtime.get("wood_text"),
            "crystals_text": runtime.get("crystals_text"),
            "diamonds_text": runtime.get("diamonds_text"),
            "od_text": runtime.get("od_text"),
        } if isinstance(runtime, dict) else {},
    }


def _history(device, limit=30):
    rows = []
    for path in (ACCOUNT_HISTORY_FILE, RESOURCE_HISTORY_FILE):
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
        except Exception:
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if str(item.get("device", "")) != device:
                continue
            ts = item.get("timestamp") or item.get("time") or item.get("created_at")
            if isinstance(ts, (int, float)):
                time_text = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M:%S")
            else:
                time_text = ""
            rows.append({
                "timestamp": ts or 0,
                "time_text": time_text,
                "action": item.get("action") or item.get("task") or item.get("label") or "",
                "note": item.get("note") or item.get("status") or "",
            })
    rows.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)
    return rows[: max(1, min(int(limit), 100))]


def _local_ips():
    result = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in result and not ip.startswith("127."):
                result.append(ip)
    except Exception:
        pass
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "MuMuRemote/0.1"

    def log_message(self, format, *args):
        return

    def _json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except Exception:
            n = 0
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _auth(self):
        header = self.headers.get("Authorization", "")
        token = _settings().get("token")
        return bool(token and header == f"Bearer {token}")

    def _require_auth(self):
        if self._auth():
            return True
        self._json({"error": "Неверный токен или сопряжение ещё не выполнено"}, 401)
        return False

    def _route(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(x) for x in parsed.path.strip("/").split("/") if x]
        return parsed, parts

    def do_GET(self):
        parsed, parts = self._route()
        if parsed.path == "/api/v1/ping":
            self._json({"ok": True, "name": "MuMu Remote PC", "version": "0.1.0"})
            return

        if not self._require_auth():
            return

        if parsed.path == "/api/v1/accounts":
            devs = _devices()
            self._json({"accounts": [_account_payload(d, i) for i, d in enumerate(devs)], "updated_at": time.time()})
            return

        if len(parts) >= 5 and parts[:3] == ["api", "v1", "accounts"]:
            device = parts[3]
            action = parts[4]
            if action == "screenshot":
                try:
                    raw = _adb("-s", device, "exec-out", "screencap", "-p", timeout=20, binary=True)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(raw)
                except Exception as exc:
                    self._json({"error": str(exc)}, 500)
                return
            if action == "history":
                q = urllib.parse.parse_qs(parsed.query)
                limit = int((q.get("limit") or ["30"])[0])
                self._json({"history": _history(device, limit)})
                return

        self._json({"error": "Неизвестный endpoint"}, 404)

    def do_POST(self):
        parsed, parts = self._route()
        body = self._body()

        if parsed.path == "/api/v1/pair":
            code = str(body.get("code", "")).strip()
            if not _PAIR_CODE or code != _PAIR_CODE:
                self._json({"error": "Неверный код сопряжения"}, 403)
                return
            self._json({"ok": True, "token": _settings()["token"]})
            return

        if not self._require_auth():
            return

        if len(parts) >= 5 and parts[:3] == ["api", "v1", "accounts"]:
            device = parts[3]
            action = "/".join(parts[4:])
            try:
                if action == "pause":
                    minutes = int(body.get("minutes", 30))
                    minutes = _set_pause(device, minutes)
                    self._json({"ok": True, "minutes": minutes})
                    return
                if action == "resume":
                    _request_resume(device)
                    self._json({"ok": True})
                    return
                if action == "game/start":
                    _start_game(device)
                    self._json({"ok": True})
                    return
                if action == "game/stop":
                    _force_stop_game(device)
                    self._json({"ok": True})
                    return
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
                return

        self._json({"error": "Неизвестный endpoint"}, 404)


def start_remote_server():
    global _SERVER, _SERVER_THREAD, _PAIR_CODE
    with _LOCK:
        if _SERVER_THREAD and _SERVER_THREAD.is_alive():
            return
        cfg = _settings()
        _PAIR_CODE = f"{random.randint(0, 999999):06d}"
        _SERVER = ThreadingHTTPServer((str(cfg.get("host", "0.0.0.0")), int(cfg.get("port", 8765))), Handler)
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, name="MuMuRemoteServer", daemon=True)
        _SERVER_THREAD.start()

        ips = _local_ips()
        lines = [
            "MuMu Remote",
            f"Код сопряжения: {_PAIR_CODE}",
            f"Порт: {cfg.get('port', 8765)}",
            "",
            "Адреса в локальной сети:",
            *[f"http://{ip}:{cfg.get('port', 8765)}" for ip in ips],
            "",
            "Код меняется после каждого перезапуска сервера.",
        ]
        PAIR_FILE.write_text("\n".join(lines), encoding="utf-8")
        print("[Remote] Сервер запущен.")
        print(f"[Remote] Код сопряжения: {_PAIR_CODE}")
        for ip in ips:
            print(f"[Remote] http://{ip}:{cfg.get('port', 8765)}")


if __name__ == "__main__":
    start_remote_server()
    print("Нажми Ctrl+C для выхода.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
