"""Save a completed Mureka task's first MP3 before its CDN link expires."""
import os
import re
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import certifi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mureka_api import MurekaApi  # noqa: E402

MAX_BYTES = 60_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def save_url(task_id, url, target_dir=None):
    if not re.fullmatch(r'[0-9]{4,30}', task_id):
        raise ValueError('Mureka 任務 ID 格式不正確。')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'cdn.mureka.ai':
        raise ValueError('音檔不在 Mureka 官方 CDN，已停止下載。')
    target_dir = Path(target_dir) if target_dir else ROOT / 'data' / 'audio'
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f'mureka-{task_id}.mp3'
    if destination.exists():
        return destination
    opener = urllib.request.build_opener(
        NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where()))
    )
    fd, temporary = tempfile.mkstemp(prefix='.mureka-download-', dir=target_dir)
    try:
        with os.fdopen(fd, 'wb') as output, opener.open(url, timeout=60) as response:
            if response.headers.get('Content-Type', '').split(';')[0] not in ('audio/mpeg', 'audio/mp3', 'application/octet-stream'):
                raise ValueError('CDN 沒有回傳 MP3 音檔。')
            total = 0
            while chunk := response.read(256 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError('音檔超出大小限制。')
                output.write(chunk)
        if total < 1024:
            raise ValueError('音檔太小，可能未下載完整。')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def save(task_id):
    if not re.fullmatch(r'[0-9]{4,30}', task_id):
        raise ValueError('Mureka 任務 ID 格式不正確。')
    result = MurekaApi().poll(task_id)
    if result['state'] != 'completed' or not result['audio_urls']:
        raise ValueError('任務尚未完成，或沒有音檔。')
    return save_url(task_id, result['audio_urls'][0])


if __name__ == '__main__':
    print(save(sys.argv[1]))
