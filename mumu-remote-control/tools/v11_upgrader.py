from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

VERSION = "11.0"
REMOTE_IMPORT_MAIN = "from remote_server import remote_pause_gate, hold_account_for_phone_login"
REMOTE_IMPORT_GUI = "from remote_server import start_remote_server"


def appdata_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "MuMuBot"


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name


def find_bot_dir() -> Path | None:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.extend([Path.cwd(), Path(__file__).resolve().parent])
    for folder in candidates:
        if (folder / "main.py").exists() and (folder / "mumu_bot_gui.py").exists():
            return folder
    return None


def choose_bot_dir() -> Path | None:
    found = find_bot_dir()
    if found:
        return found
    root = tk.Tk()
    root.withdraw()
    selected = filedialog.askdirectory(
        title="Выбери папку MuMu Bot, где лежат main.py и mumu_bot_gui.py"
    )
    root.destroy()
    if not selected:
        return None
    folder = Path(selected)
    if not (folder / "main.py").exists() or not (folder / "mumu_bot_gui.py").exists():
        messagebox.showerror(
            "MuMu Bot v11",
            "В выбранной папке не найдены main.py и mumu_bot_gui.py.",
        )
        return None
    return folder


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".v11tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def replace_top_level_function(text: str, name: str, replacement: str) -> str:
    lines = text.splitlines(True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        raise RuntimeError(f"Не найдена функция {name}()")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if (line.startswith("def ") or line.startswith("class ")) and line.strip():
            end = j
            break

    repl = replacement.rstrip() + "\n\n"
    return "".join(lines[:start]) + repl + "".join(lines[end:])


def replace_remote_import(text: str, wanted: str) -> str:
    lines = text.splitlines(True)
    cleaned = []
    for line in lines:
        if line.lstrip().startswith("from remote_server import "):
            continue
        cleaned.append(line)

    insert = 0
    for i, line in enumerate(cleaned):
        if line.startswith("from __future__ import "):
            insert = i + 1
            break
    cleaned.insert(insert, wanted + "\n")
    return "".join(cleaned)


def insert_function_preamble(text: str, name: str, code_lines: list[str], marker: str) -> str:
    if marker in text:
        return text
    lines = text.splitlines(True)
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        raise RuntimeError(f"Не найдена функция {name}()")

    signature_end = None
    for j in range(start, min(len(lines), start + 30)):
        if lines[j].rstrip().endswith("):"):
            signature_end = j
            break
    if signature_end is None:
        raise RuntimeError(f"Не удалось определить начало {name}()")

    block = "".join("    " + line.rstrip() + "\n" for line in code_lines) + "\n"
    lines.insert(signature_end + 1, block)
    return "".join(lines)


def remove_statement(lines: list[str], anchor: str) -> tuple[list[str], bool]:
    out = []
    i = 0
    removed = False
    while i < len(lines):
        if anchor not in lines[i]:
            out.append(lines[i])
            i += 1
            continue

        removed = True
        balance = 0
        saw_paren = False
        while i < len(lines):
            line = lines[i]
            balance += line.count("(") - line.count(")")
            saw_paren = saw_paren or "(" in line
            i += 1
            if saw_paren and balance <= 0:
                break
    return out, removed


def remove_telegram_button(text: str) -> str:
    lines = text.splitlines(True)
    lines, _ = remove_statement(lines, "self.telegram_button =")
    lines, _ = remove_statement(lines, "self.telegram_button.grid(")
    return "".join(lines)


def patch_gui(text: str) -> str:
    text = replace_remote_import(text, REMOTE_IMPORT_GUI)
    text = remove_telegram_button(text)

    if "start_remote_server()" not in text:
        lines = text.splitlines(True)
        class_start = None
        for i, line in enumerate(lines):
            if line.startswith("class MuMuBotGUI"):
                class_start = i
                break
        if class_start is None:
            raise RuntimeError("Не найден class MuMuBotGUI")

        init_start = None
        for i in range(class_start + 1, min(len(lines), class_start + 200)):
            if re.match(r"^\s+def __init__\(", lines[i]):
                init_start = i
                break
        if init_start is None:
            raise RuntimeError("Не найден MuMuBotGUI.__init__")

        signature_end = None
        for i in range(init_start, min(len(lines), init_start + 30)):
            if lines[i].rstrip().endswith("):"):
                signature_end = i
                break
        if signature_end is None:
            raise RuntimeError("Не удалось определить начало MuMuBotGUI.__init__")

        indent = re.match(r"^(\s*)", lines[init_start]).group(1) + "    "
        block = (
            indent + "try:\n"
            + indent + "    start_remote_server()\n"
            + indent + "except Exception as exc:\n"
            + indent + "    print(f'[Remote] Не удалось запустить встроенный сервер: {exc}')\n\n"
        )
        lines.insert(signature_end + 1, block)
        text = "".join(lines)

    return text


DISABLED_TELEGRAM = '''def load_telegram_settings():
    # Telegram удалён из пользовательской сборки v11.
    return {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "interval_minutes": 120,
        "send_screenshots": False,
    }
'''

NO_LOGIN_PENDING = '''def login_approval_pending(device):
    # Старый Telegram approval больше не используется.
    return False
'''

PHONE_LOGIN_HOLD = '''def queue_login_approval(
    device,
    context,
    result,
    image_bytes
):
    try:
        hold_account_for_phone_login(device, minutes=60)
    except Exception as exc:
        print(f"[{device}] Remote: не удалось поставить аккаунт на паузу: {exc}")

    try:
        state = _load_login_approval_state()
        devices = state.setdefault("devices", {})
        devices.pop(device, None)
        _save_login_approval_state(state)
    except Exception:
        pass

    print(
        f"[{device}] Вход с другого устройства: "
        "ПК больше не забирает аккаунт обратно. "
        "Аккаунт остановлен до ручного возобновления из MuMu Remote."
    )
    raise LoginApprovalPending(
        "вход с другого устройства: аккаунт остановлен до возобновления из MuMu Remote"
    )
'''

NO_OLD_APPROVAL = '''def process_pending_login_approval(device):
    # В v11 управление конфликтом входа перенесено в MuMu Remote.
    return True
'''

NO_TELEGRAM_CALLBACKS = '''def telegram_poll_login_approval_callbacks():
    return None
'''


def patch_main(text: str) -> str:
    text = replace_remote_import(text, REMOTE_IMPORT_MAIN)

    # Если старый установщик уже запускал сервер внутри engine-процесса,
    # убираем это. Сервер теперь живёт только в GUI-процессе.
    text = re.sub(r"(?m)^[ \t]*start_remote_server\(\)[ \t]*\n", "", text)

    text = insert_function_preamble(
        text,
        "run_device_due_actions",
        [
            "if remote_pause_gate(device):",
            "    return",
        ],
        "remote_pause_gate(device)",
    )

    for name, replacement in (
        ("load_telegram_settings", DISABLED_TELEGRAM),
        ("login_approval_pending", NO_LOGIN_PENDING),
        ("queue_login_approval", PHONE_LOGIN_HOLD),
        ("process_pending_login_approval", NO_OLD_APPROVAL),
    ):
        try:
            text = replace_top_level_function(text, name, replacement)
        except RuntimeError:
            if name in {"load_telegram_settings", "queue_login_approval"}:
                raise

    try:
        text = replace_top_level_function(
            text,
            "telegram_poll_login_approval_callbacks",
            NO_TELEGRAM_CALLBACKS,
        )
    except RuntimeError:
        pass

    # На случай более новой версии, где callback вызывается в другом месте.
    text = re.sub(
        r"(?m)^[ \t]*telegram_poll_login_approval_callbacks\(\)[ \t]*\n",
        "",
        text,
    )

    return text


def backup_files(bot_dir: Path, paths: list[Path]) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = appdata_dir() / "Backups" / f"v11_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            rel = path.name
            shutil.copy2(path, backup / rel)
    return backup


def cleanup_root(bot_dir: Path, backup: Path) -> list[str]:
    removed = []
    patterns = (
        "START_DEBUG*.bat",
        "CHECK_START*.bat",
        "START_SERVER*.bat",
        "INSTALL_REMOTE_SERVER*.bat",
        "*SERVER_DEBUG*.bat",
        "README*.md",
        "README*.txt",
        "REMOTE_PAIRING.txt",
        "install_into_mumubot.py",
    )

    for pattern in patterns:
        for path in bot_dir.glob(pattern):
            if path.name.lower() in {"readme_LICENSE.md".lower()}:
                continue
            try:
                if path.is_file():
                    dest = backup / ("removed_" + path.name)
                    if not dest.exists():
                        shutil.copy2(path, dest)
                    path.unlink()
                    removed.append(path.name)
            except Exception:
                pass

    # Telegram больше не используется. Старые данные сохраняем только в backup.
    for name in (
        "telegram_settings.json",
        "telegram_control_state.json",
        "telegram_report_state.json",
    ):
        path = bot_dir / name
        if path.exists():
            try:
                dest = backup / ("removed_" + path.name)
                if not dest.exists():
                    shutil.copy2(path, dest)
                path.unlink()
                removed.append(path.name)
            except Exception:
                pass

    # Пользователь запускает START.vbs. Остальные START*.vbs убираем.
    for path in bot_dir.glob("START*.vbs"):
        if path.name.upper() == "START.VBS":
            continue
        try:
            dest = backup / ("removed_" + path.name)
            if not dest.exists():
                shutil.copy2(path, dest)
            path.unlink()
            removed.append(path.name)
        except Exception:
            pass

    # Технический BAT, если нужен текущему START.vbs, оставляем, но скрываем.
    start_bat = bot_dir / "start_gui.bat"
    if start_bat.exists():
        try:
            os.system(f'attrib +h "{start_bat}" >nul 2>nul')
        except Exception:
            pass

    return removed


def install(bot_dir: Path) -> tuple[Path, list[str]]:
    main_py = bot_dir / "main.py"
    gui_py = bot_dir / "mumu_bot_gui.py"
    start_vbs = bot_dir / "START.vbs"

    if not main_py.exists() or not gui_py.exists():
        raise RuntimeError("Не найдены main.py и mumu_bot_gui.py")

    remote_src = resource_path("remote_server.py")
    if not remote_src.exists():
        raise RuntimeError("В обновлятор не попал remote_server.py")

    old_main = read_text(main_py)
    old_gui = read_text(gui_py)

    new_main = patch_main(old_main)
    new_gui = patch_gui(old_gui)

    # Проверяем синтаксис ДО изменения файлов пользователя.
    compile(new_main, str(main_py), "exec")
    compile(new_gui, str(gui_py), "exec")
    compile(read_text(remote_src), str(remote_src), "exec")

    backup = backup_files(bot_dir, [main_py, gui_py, start_vbs, bot_dir / "remote_server.py"])

    write_text_atomic(main_py, new_main)
    write_text_atomic(gui_py, new_gui)
    shutil.copy2(remote_src, bot_dir / "remote_server.py")
    try:
        os.system(f'attrib +h "{bot_dir / "remote_server.py"}" >nul 2>nul')
    except Exception:
        pass

    removed = cleanup_root(bot_dir, backup)
    return backup, removed


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    try:
        bot_dir = find_bot_dir()
        if bot_dir is None:
            selected = filedialog.askdirectory(
                title="Выбери папку MuMu Bot, где лежат main.py и mumu_bot_gui.py"
            )
            if not selected:
                return 1
            bot_dir = Path(selected)

        if not (bot_dir / "main.py").exists() or not (bot_dir / "mumu_bot_gui.py").exists():
            messagebox.showerror(
                "MuMu Bot v11",
                "Это не папка MuMu Bot: не найдены main.py и mumu_bot_gui.py.",
            )
            return 2

        backup, removed = install(bot_dir)
        details = ""
        if removed:
            details = "\n\nУбрано из папки: " + ", ".join(sorted(removed))
        messagebox.showinfo(
            "MuMu Bot v11",
            "Готово.\n\n"
            "Теперь запускай как обычно через START.vbs.\n"
            "Remote Server стартует автоматически вместе с MuMu Bot.\n"
            "Telegram отключён.\n"
            "Резервная копия сохранена в:\n"
            f"{backup}"
            + details,
        )
        return 0
    except Exception as exc:
        messagebox.showerror(
            "MuMu Bot v11 — ошибка",
            "Ничего специально доделывать вручную не нужно.\n\n"
            "Обновление остановлено:\n"
            f"{exc}",
        )
        return 3
    finally:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
