"""Prompt for a Mureka API key without echo or shell-history exposure."""
import getpass
import os
import tempfile
from pathlib import Path


DESTINATION = Path(__file__).resolve().parents[1] / 'data' / '.mureka_api_key'


def main():
    key = getpass.getpass('貼上 Mureka API key（輸入不會顯示），按 Enter：').strip()
    if len(key) < 10 or any(c.isspace() for c in key):
        raise SystemExit('未儲存：key 太短或包含空白。')
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.mureka-key-', dir=DESTINATION.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(key)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, DESTINATION)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('已安全儲存到本機；程式需重新啟動才會讀取。')


if __name__ == '__main__':
    main()
