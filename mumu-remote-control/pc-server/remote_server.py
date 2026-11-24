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
REMOTE_DIR = Path(os.getenv("APPDATA") or APP_DIR) / "MuMuBot" / "Remote"
REMOTE_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = REMOTE_DIR / "settings.json"
PAUSE_FILE = REMOTE_DIR / "pause_state.json"
BOT_RUNTIME_FILE = APP_DIR / "bot_runtime.json"
BOT_STATE_FILE = APP_DIR / "bot_state.json"
GUI_SETTINGS_FILE = APP_DIR / "gui_settings.json"
ACCOUNT_HISTORY_FILE = APP_DIR / "account_history.jsonl"
RESOURCE_HISTORY_FILE = APP_DIR / "resource_history.jsonl"

_SERVER = None
_SERVER_THREAD = None
_DISCOVERY_THREAD = None
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
    data.setdefault("mumu_manager_path", "")
    _atomic_write_json(SETTINGS_FILE, data)
    return data


def _find_mumu_manager():
    configured = _settings().get("mumu_manager_path")
    if configured and Path(str(configured)).exists():
        return str(configured)

    bases = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    candidates = []
    for base in bases:
        candidates.extend([
            base / "Netease" / "MuMuPlayer" / "nx_main" / "MuMuManager.exe",
            base / "Netease" / "MuMuPlayer-12.0" / "nx_main" / "MuMuManager.exe",
            base / "Netease" / "MuMuPlayerGlobal-12.0" / "nx_main" / "MuMuManager.exe",
        ])
        netease = base / "Netease"
        try:
            if netease.exists():
                candidates.extend(netease.glob("MuMu*/nx_main/MuMuManager.exe"))
        except Exception:
            pass

    for item in candidates:
        try:
            if item.exists():
                return str(item)
        except Exception:
            pass
    return ""


def _manager_instances():
    manager = _find_mumu_manager()
    if not manager:
        return None
    try:
        cp = subprocess.run(
            [manager, "info", "-v", "all"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = cp.stdout.decode("utf-8", "ignore").strip()
        if cp.returncode != 0 or not raw:
            return None
        data = json.loads(raw)
    except Exception:
        return None

    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = [data] if "index" in data else list(data.values())
    else:
        return None

    found = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            index = int(record.get("index"))
        except Exception:
            continue
        started = bool(
            record.get("is_android_started")
            or record.get("is_process_started")
            or record.get("player_state") == "start_finished"
            or record.get("adb_port")
        )
        host = str(record.get("adb_host_ip") or "127.0.0.1")
        port = record.get("adb_port")
        try:
            port = int(port) if port else None
        except Exception:
            port = None
        if started and not port:
            port = 16384 + 32 * index
        serial = f"{host}:{port}" if port else ""
        found.append({
            "id": f"mumu:{index}",
            "vm_index": index,
            "name": str(record.get("name") or f"MuMu {index + 1}"),
            "started": started,
            "serial": serial,
        })
    return sorted(found, key=lambda x: x["vm_index"])


def _manager_control(index, action):
    manager = _find_mumu_manager()
    if not manager:
        raise RuntimeError("MuMuManager.exe не найден")
    cp = subprocess.run(
        [manager, "control", "-v", str(int(index)), str(action)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    raw = cp.stdout.decode("utf-8", "ignore").strip()
    reply = {}
    try:
        reply = json.loads(raw) if raw else {}
    except Exception:
        reply = {}
    if cp.returncode != 0 or (isinstance(reply, dict) and reply.get("errcode") not in (None, 0)):
        msg = reply.get("errmsg") if isinstance(reply, dict) else ""
        raise RuntimeError(msg or cp.stderr.decode("utf-8", "ignore").strip() or "MuMuManager command failed")
    return True


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


def _looks_like_device(value):
    value = str(value or "").strip()
    if not value:
        return False
    if value.startswith("127.0.0.1:") and value.rsplit(":", 1)[-1].isdigit():
        return True
    if value.startswith("emulator-") and value[9:].isdigit():
        return True
    return False


def _extract_devices(value, out):
    if isinstance(value, dict):
        for key, item in value.items():
            if _looks_like_device(key):
                out.add(str(key))
            _extract_devices(item, out)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _extract_devices(item, out)
    elif _looks_like_device(value):
        out.add(str(value))


def _connected_devices():
    result = set()
    runtime = _read_json(BOT_RUNTIME_FILE, {})
    if isinstance(runtime, dict):
        active = runtime.get("active_devices")
        if isinstance(active, list):
            result.update(str(x) for x in active if _looks_like_device(x))
    try:
        out = _adb("devices")
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device" and _looks_like_device(parts[0]):
                result.add(parts[0])
    except Exception:
        pass
    return result


def _known_devices():
    found = set()
    for path in (GUI_SETTINGS_FILE, BOT_STATE_FILE, BOT_RUNTIME_FILE, PAUSE_FILE):
        _extract_devices(_read_json(path, {}), found)
    found.update(_gui_name_map().keys())
    found.update(_connected_devices())

    def sort_key(device):
        try:
            return (0, int(str(device).rsplit(":", 1)[-1]))
        except Exception:
            return (1, str(device))
    return sorted((d for d in found if _looks_like_device(d)), key=sort_key)


def _account_records():
    connected = _connected_devices()
    names = _gui_name_map()
    manager_records = _manager_instances()
    result = []
    covered = set()

    if manager_records is not None:
        for rec in manager_records:
            serial = rec.get("serial") or ""
            if serial:
                covered.add(serial)
            display_name = names.get(serial) or rec.get("name") or f"Аккаунт {len(result) + 1}"
            result.append({
                "id": rec["id"],
                "device": serial,
                "vm_index": rec["vm_index"],
                "name": display_name,
                "mumu_running": bool(rec.get("started")),
                "online": bool(serial and serial in connected),
            })

    for device in _known_devices():
        if device in covered:
            continue
        result.append({
            "id": device,
            "device": device,
            "vm_index": None,
            "name": names.get(device) or f"Аккаунт {len(result) + 1}",
            "mumu_running": device in connected,
            "online": device in connected,
        })
    return result


def _resolve_account(account_id):
    account_id = str(account_id or "")
    if account_id.startswith("mumu:"):
        try:
            index = int(account_id.split(":", 1)[1])
        except Exception:
            raise RuntimeError("Неверный индекс MuMu")
        records = _manager_instances() or []
        for rec in records:
            if rec.get("vm_index") == index:
                return rec
        raise RuntimeError("Экземпляр MuMu не найден")
    return {"id": account_id, "vm_index": None, "serial": account_id, "started": True, "name": account_id}


def _resolve_device(account_id):
    rec = _resolve_account(account_id)
    serial = str(rec.get("serial") or "")
    if not serial:
        raise RuntimeError("MuMu сейчас выключен или ADB ещё не готов")
    return serial


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


def hold_account_for_phone_login(device, minutes=60):
    """Stop the PC game and hold this account until the user resumes it from MuMu Remote."""
    minutes = max(1, min(int(minutes), 1440))
    _set_pause(device, minutes)
    with _LOCK:
        data = _pause_state()
        entry = data["devices"].setdefault(device, {})
        entry["reason"] = "phone_login"
        entry["manual_resume_only"] = True
        entry["paused_until"] = max(float(entry.get("paused_until") or 0), time.time() + minutes * 60)
        try:
            _force_stop_game(device)
            entry["game_stopped"] = True
            entry["stopped_at"] = time.time()
        except Exception as exc:
            entry["stop_error"] = str(exc)[:500]
        _atomic_write_json(PAUSE_FILE, data)
    print(f"[Remote] {device}: обнаружен вход с другого устройства; ПК остановлен до ручного возобновления.")
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

        manual_resume_only = bool(entry.get("manual_resume_only"))
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

        if manual_resume_only and not resume_requested:
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


def _account_payload(record, index, connected=None):
    connected = connected if isinstance(connected, set) else _connected_devices()
    account_id = str(record.get("id") or "")
    device = str(record.get("device") or "")
    online = bool(record.get("online")) and bool(device in connected)
    mumu_running = bool(record.get("mumu_running"))
    runtime = _runtime_device(device) if device else {}
    pause = _entry(device) if device else None
    paused_until = float(pause.get("paused_until") or 0) if isinstance(pause, dict) else 0
    remaining = max(0, int(paused_until - time.time()))
    manual_hold = bool(pause.get("manual_resume_only")) if isinstance(pause, dict) else False
    if manual_hold:
        status = "phone_hold"
    elif remaining > 0:
        status = "paused"
    elif online:
        status = "online"
    elif mumu_running:
        status = "starting"
    else:
        status = "offline"
    return {
        "id": account_id or device,
        "device": device,
        "vm_index": record.get("vm_index"),
        "name": str(record.get("name") or f"Аккаунт {index + 1}"),
        "online": online,
        "mumu_running": mumu_running,
        "status": status,
        "paused": remaining > 0 or manual_hold,
        "manual_resume_only": manual_hold,
        "pause_reason": pause.get("reason") if isinstance(pause, dict) else "",
        "pause_remaining_seconds": remaining,
        "active_task": _active_task(device) if online and device else "",
        "foreground_app": runtime.get("foreground_app") if online and isinstance(runtime, dict) else None,
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


def _tailscale_ipv4():
    candidates = [
        "tailscale",
        r"C:\\Program Files\\Tailscale\\tailscale.exe",
        r"C:\\Program Files (x86)\\Tailscale\\tailscale.exe",
    ]
    for exe in candidates:
        try:
            cp = subprocess.run([exe, "ip", "-4"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4, check=False)
            if cp.returncode == 0:
                for line in cp.stdout.decode("utf-8", "ignore").splitlines():
                    ip = line.strip()
                    if ip.startswith("100."):
                        return ip
        except Exception:
            continue
    return ""


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


def _discovery_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", 8766))
        while True:
            try:
                data, address = sock.recvfrom(4096)
                if data.decode("utf-8", "ignore").strip() != "MUMU_REMOTE_DISCOVER_V1":
                    continue
                cfg = _settings()
                payload = json.dumps({
                    "name": "MuMu Bot",
                    "port": int(cfg.get("port", 8765)),
                    "pair_code": _PAIR_CODE or "",
                    "tailscale_url": (
                        f"http://{_tailscale_ipv4()}:{int(cfg.get('port', 8765))}"
                        if _tailscale_ipv4() else ""
                    ),
                }, ensure_ascii=False).encode("utf-8")
                sock.sendto(payload, address)
            except Exception:
                time.sleep(0.2)
    finally:
        try:
            sock.close()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = "MuMuRemote/0.2"

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
            self._json({"ok": True, "name": "MuMu Remote PC", "version": "0.2.0"})
            return

        if not self._require_auth():
            return

        if parsed.path == "/api/v1/accounts":
            connected = _connected_devices()
            records = _account_records()
            self._json({"accounts": [_account_payload(rec, i, connected) for i, rec in enumerate(records)], "updated_at": time.time()})
            return

        if len(parts) >= 5 and parts[:3] == ["api", "v1", "accounts"]:
            account_id = parts[3]
            action = parts[4]
            if action == "screenshot":
                try:
                    device = _resolve_device(account_id)
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
                try:
                    device = _resolve_device(account_id)
                    rows = _history(device, limit)
                except Exception:
                    rows = []
                self._json({"history": rows})
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
            port = int(_settings().get("port", 8765))
            local_urls = [f"http://{ip}:{port}" for ip in _local_ips()]
            tailscale_ip = _tailscale_ipv4()
            self._json({
                "ok": True,
                "token": _settings()["token"],
                "local_urls": local_urls,
                "tailscale_url": f"http://{tailscale_ip}:{port}" if tailscale_ip else "",
            })
            return

        if not self._require_auth():
            return

        if len(parts) >= 5 and parts[:3] == ["api", "v1", "accounts"]:
            account_id = parts[3]
            action = "/".join(parts[4:])
            try:
                if action == "emulator/start":
                    rec = _resolve_account(account_id)
                    if rec.get("vm_index") is None:
                        raise RuntimeError("Для этого аккаунта неизвестен индекс MuMu")
                    _manager_control(rec["vm_index"], "launch")
                    self._json({"ok": True})
                    return
                if action == "emulator/stop":
                    rec = _resolve_account(account_id)
                    if rec.get("vm_index") is None:
                        raise RuntimeError("Для этого аккаунта неизвестен индекс MuMu")
                    _manager_control(rec["vm_index"], "shutdown")
                    self._json({"ok": True})
                    return

                device = _resolve_device(account_id)
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
    global _SERVER, _SERVER_THREAD, _DISCOVERY_THREAD, _PAIR_CODE
    with _LOCK:
        if _SERVER_THREAD and _SERVER_THREAD.is_alive():
            return
        cfg = _settings()
        _PAIR_CODE = f"{random.randint(0, 999999):06d}"
        _SERVER = ThreadingHTTPServer((str(cfg.get("host", "0.0.0.0")), int(cfg.get("port", 8765))), Handler)
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, name="MuMuRemoteServer", daemon=True)
        _SERVER_THREAD.start()
        if not _DISCOVERY_THREAD or not _DISCOVERY_THREAD.is_alive():
            _DISCOVERY_THREAD = threading.Thread(target=_discovery_loop, name="MuMuRemoteDiscovery", daemon=True)
            _DISCOVERY_THREAD.start()

        ips = _local_ips()
        tailscale_ip = _tailscale_ipv4()
        print("[Remote] Сервер запущен.")
        print(f"[Remote] Код сопряжения: {_PAIR_CODE}")
        for ip in ips:
            print(f"[Remote] http://{ip}:{cfg.get('port', 8765)}")
        if tailscale_ip:
            print(f"[Remote] Tailscale: http://{tailscale_ip}:{cfg.get('port', 8765)}")


if __name__ == "__main__":
    start_remote_server()
    print("Нажми Ctrl+C для выхода.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
