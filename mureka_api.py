"""Official Mureka music API adapter; secrets stay server-side."""
import json
import os
import re
import ssl
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import certifi
from mureka_brief import compile_brief


BASE_URL = 'https://api.mureka.ai'
MODELS = {'auto', 'mureka-7.6', 'mureka-o2', 'mureka-8', 'mureka-9', 'mureka-9.5'}
FAILED = {'failed', 'timeouted', 'cancelled'}
KEY_FILE = Path(__file__).parent / 'data' / '.mureka_api_key'


class MurekaApiError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class MurekaApi:
    def __init__(self, key=None, opener=None, key_file=KEY_FILE):
        if key is not None:
            self.key = key
        elif os.environ.get('MUREKA_API_KEY'):
            self.key = os.environ['MUREKA_API_KEY']
        elif Path(key_file).is_symlink():
            self.key = ''
            self.config_error = 'Mureka key 檔案不可為符號連結。'
        elif Path(key_file).is_file():
            mode = stat.S_IMODE(Path(key_file).stat().st_mode)
            if mode & 0o077:
                self.key = ''
                self.config_error = 'Mureka key 檔案權限過寬；請設為僅本人可讀。'
            else:
                self.key = Path(key_file).read_text(encoding='utf-8').strip()
        else:
            self.key = ''
        self.opener = opener or urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where()))
        ).open
        if not getattr(self, 'config_error', None):
            self.config_error = None if self.key else '尚未設定 MUREKA_API_KEY；目前不會送件或扣點。'

    def _request(self, method, path, payload=None):
        if self.config_error:
            raise MurekaApiError(self.config_error)
        headers = {'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=30) as response:
                if urllib.parse.urlparse(response.geturl()).hostname != 'api.mureka.ai':
                    raise MurekaApiError('Mureka API 重新導向至其他主機；已停止。')
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise MurekaApiError('Mureka API 回應超出大小限制。')
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise MurekaApiError('Mureka API 回應格式不正確。')
                return result
        except urllib.error.HTTPError as exc:
            raise MurekaApiError(f'Mureka API 回應 HTTP {exc.code}；請檢查 API key、額度與帳號權限。') from exc
        except urllib.error.URLError as exc:
            raise MurekaApiError('無法連線到 Mureka 官方 API。') from exc
        except (ValueError, TypeError) as exc:
            raise MurekaApiError('Mureka API 回應不是有效 JSON。') from exc

    def verify(self):
        """Read-only billing query; does not request music generation."""
        self._request('GET', '/v1/account/billing')
        return True

    @staticmethod
    def payload(song, model='auto', n=1):
        if model not in MODELS or type(n) is not int or not 1 <= n <= 3:
            raise MurekaApiError('Mureka 模型或版本數量不正確。')
        if (song.get('_payload') or {}).get('instrumental'):
            raise MurekaApiError('純音樂不能使用歌詞轉歌曲端點；請先改為有人聲歌曲。')
        lyrics = str(song.get('lyrics') or '').strip()
        if not lyrics or len(lyrics) > 5000:
            raise MurekaApiError('歌詞須為 1–5000 字；純音樂需使用另一個 API 流程。')
        try:
            prompt = compile_brief(song)
        except ValueError as exc:
            raise MurekaApiError(str(exc)) from exc
        body = {'lyrics': lyrics, 'model': model, 'n': n, 'prompt': prompt}
        gender = str(song.get('vocal_gender') or '').lower()
        if gender in ('male', 'female'):
            body['gender'] = gender
        vocal_id = str(song.get('_mureka_vocal_id') or '').strip()
        if vocal_id:
            if model == 'mureka-o2':
                raise MurekaApiError('Mureka O2 不支援 Vocal ID；請選 Auto 或其他支援模型。')
            if not re.fullmatch(r'[A-Za-z0-9_-]{3,128}', vocal_id):
                raise MurekaApiError('Vocal ID 格式不正確。')
            body['vocal_id'] = vocal_id
        return body

    def submit(self, song, model='auto', n=1):
        body = self.payload(song, model, n)
        response = self._request('POST', '/v1/song/generate', body)
        task_id = response.get('id')
        if not isinstance(task_id, (str, int)) or not str(task_id):
            raise MurekaApiError('送件結果不明；可能已扣點，請到 Mureka API 平台核對，勿重送。')
        return str(task_id)

    def poll(self, external_id):
        path = '/v1/song/query/' + urllib.parse.quote(str(external_id), safe='')
        result = self._request('GET', path)
        state = result.get('status')
        if state in FAILED:
            return {'state': 'failed', 'external_id': external_id,
                    'reason': str(result.get('failed_reason') or state)[:300]}
        if state != 'succeeded':
            if state not in ('preparing', 'queued', 'running', 'streaming'):
                raise MurekaApiError('Mureka 查詢回應缺少已知狀態。')
            return {'state': 'pending', 'external_id': external_id}
        choices = result.get('choices')
        if not isinstance(choices, list):
            raise MurekaApiError('Mureka 已完成，但回應缺少版本清單。')
        versions = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            urls = {name: choice[name] for name in ('url', 'mp3_url', 'wav_url')
                    if isinstance(choice.get(name), str) and choice[name].startswith('https://')}
            versions.append({'id': choice.get('id'), 'urls': urls})
        audio_urls = [v['urls'].get('url') or v['urls'].get('mp3_url') or v['urls'].get('wav_url')
                      for v in versions if v['urls']]
        if not audio_urls:
            raise MurekaApiError('Mureka 已完成，但尚未取得可播放的 HTTPS 音檔；可稍後重查。')
        return {'state': 'completed', 'external_id': external_id,
                'audio_urls': audio_urls, 'versions': versions}
