from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fail(msg):
    print("ОШИБКА:", msg)
    raise SystemExit(1)


def main():
    if len(sys.argv) > 1:
        target = Path(sys.argv[1]).expanduser().resolve()
    else:
        raw = input("Путь к папке MuMuBot (где лежит main.py): ").strip().strip('"')
        target = Path(raw).expanduser().resolve()

    main_py = target / "main.py"
    if not main_py.exists():
        fail(f"Не найден {main_py}")

    remote_src = HERE / "remote_server.py"
    if not remote_src.exists():
        fail("Рядом с установщиком нет remote_server.py")

    shutil.copy2(remote_src, target / "remote_server.py")

    text = main_py.read_text(encoding="utf-8-sig")
    original = text

    import_line = "from remote_server import start_remote_server, remote_pause_gate\n"
    if import_line.strip() not in text:
        insert_at = 0
        lines = text.splitlines(True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                insert_at = i + 1
            elif insert_at and stripped and not stripped.startswith("#"):
                break
        lines.insert(insert_at, import_line)
        text = "".join(lines)

    marker = "def run_device_due_actions("
    pos = text.find(marker)
    if pos < 0:
        fail("Не нашёл run_device_due_actions() — пришли main.py, подстроим патч.")
    body_pos = text.find("):", pos)
    if body_pos < 0:
        fail("Не удалось разобрать run_device_due_actions().")
    newline = text.find("\n", body_pos)
    gate = "    if remote_pause_gate(device):\n        return\n\n"
    window = text[newline + 1:newline + 1 + 300]
    if "remote_pause_gate(device)" not in window:
        text = text[:newline + 1] + gate + text[newline + 1:]

    main_pos = text.find("def main():")
    if main_pos < 0:
        fail("Не нашёл main().")
    main_nl = text.find("\n", main_pos)
    start_line = "    start_remote_server()\n"
    if "start_remote_server()" not in text[main_nl + 1: main_nl + 400]:
        text = text[:main_nl + 1] + start_line + text[main_nl + 1:]

    if text == original:
        print("main.py уже был настроен. remote_server.py обновлён.")
        return

    backup = target / "main_before_remote.py"
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    main_py.write_text(text, encoding="utf-8")
    print("ГОТОВО.")
    print("Добавлен remote_server.py")
    print("main.py подключён к удалённому серверу и паузам.")
    print("Резервная копия:", backup)


if __name__ == "__main__":
    main()
