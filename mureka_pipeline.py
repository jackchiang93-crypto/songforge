"""Durable submission queue for the official Mureka API adapter."""
import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from production import now
from mureka_api import MODELS, MurekaApiError
from mureka_brief import co_prompt
from scripts.save_mureka_audio import save_url


class MurekaPipeline:
    def __init__(self, studio, api, wait=time.sleep):
        self.studio, self.api, self.wait = studio, api, wait
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mureka-submission')
        with self.studio.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS mureka_tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        for task in self.tasks():
            if task['status'] == 'submitting':
                task.update(status='uncertain', message='送件時服務中斷；請在 Mureka API 平台核對，勿再次送件。')
                self.write(task)
            elif task['status'] in ('queued', 'polling'):
                task.update(status='paused', message='服務曾中斷；可繼續未送出的工作或查詢既有任務。')
                self.write(task)

    def tasks(self):
        with self.studio.db() as db:
            tasks = [json.loads(row[0]) for row in db.execute('SELECT data FROM mureka_tasks ORDER BY rowid DESC')]
        for task in tasks:
            external_id = str(task.get('external_id') or '')
            if task.get('status') == 'completed' and re.fullmatch(r'[0-9]{4,30}', external_id) and self.audio_path(external_id).is_file():
                task['local_audio_url'] = f'/api/mureka-production/audio/{external_id}'
        return tasks

    def audio_path(self, external_id):
        return Path(self.studio.path).parent / 'audio' / f'mureka-{external_id}.mp3'

    def preserve_audio(self, task):
        """Best-effort local copy; a download error never repeats generation."""
        if not re.fullmatch(r'[0-9]{4,30}', str(task.get('external_id') or '')):
            return
        urls = (task.get('result') or {}).get('audio_urls') or []
        if not urls:
            return
        try:
            save_url(str(task['external_id']), urls[0], self.audio_path(task['external_id']).parent)
            task['message'] = '音樂已生成並保存本機 MP3；請試聽。'
        except Exception as exc:
            task['message'] = '音樂已生成，但本機保存失敗；請使用來源連結並稍後重試。'
            task['download_error'] = str(exc)[:200]
        self.write(task)

    def task(self, tid):
        with self.studio.db() as db:
            row = db.execute('SELECT data FROM mureka_tasks WHERE id=?', (tid,)).fetchone()
        if not row:
            raise HTTPException(404, '找不到 Mureka 工作')
        return json.loads(row[0])

    def write(self, task):
        task['updated_at'] = now()
        with self.studio.db() as db:
            db.execute('INSERT INTO mureka_tasks VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (task['id'], json.dumps(task, ensure_ascii=False)))

    def status(self):
        if self.api.config_error:
            return {'connected': False, 'reason': self.api.config_error}
        try:
            self.api.verify()
            return {'connected': True, 'reason': 'API key 已驗證；請先在 Mureka API Billing 確認有可用額度，送件會依版本計費。'}
        except MurekaApiError as exc:
            return {'connected': False, 'reason': str(exc)}

    def submit(self, song_ids, model='auto', versions=1):
        if not song_ids or len(song_ids) > 15 or len(set(song_ids)) != len(song_ids):
            raise HTTPException(422, '請選擇 1–15 首不同歌曲。')
        if model not in MODELS or type(versions) is not int or not 1 <= versions <= 3:
            raise HTTPException(422, '請選擇有效模型與 1–3 個版本。')
        connection = self.status()
        if not connection['connected']:
            raise HTTPException(503, connection['reason'])
        with self.lock:
            songs = {s['_id']: s for s in self.studio.songs()}
            existing = self.tasks()
            for sid in song_ids:
                if any(t['song_id'] == sid for t in existing):
                    title = songs.get(sid, {}).get('title', sid)
                    raise HTTPException(409, f'「{title}」已有送件紀錄，請先查看狀態。')
                if sid not in songs or not songs[sid].get('_queued'):
                    raise HTTPException(422, '有歌曲尚未入選 Mureka 待製作清單。')
                try:
                    self.api.payload(songs[sid], model, versions)
                except MurekaApiError as exc:
                    raise HTTPException(422, f'「{songs[sid].get("title", sid)}」：{exc}') from exc
            tasks = []
            for sid in song_ids:
                task = dict(id=str(uuid.uuid4()), song_id=sid, title=songs[sid]['title'],
                            snapshot=songs[sid], model=model, versions=versions,
                            status='queued', external_id=None,
                            result=None, message='等待送件', created_at=now())
                self.write(task)
                tasks.append(task)
            for task in tasks:
                self.pool.submit(self.run, task['id'])
            return tasks

    def import_completed(self, song_id, external_id):
        """Link an existing Mureka Co result without starting another paid generation."""
        if not isinstance(song_id, str) or not isinstance(external_id, str) or not re.fullmatch(r'[0-9]{4,30}', external_id):
            raise HTTPException(422, '請提供歌曲 ID 與 Mureka 數字任務 ID。')
        with self.lock:
            songs = {s['_id']: s for s in self.studio.songs()}
            song = songs.get(song_id)
            if not song:
                raise HTTPException(404, '作品庫找不到這首歌。')
            existing = next((t for t in self.tasks() if t.get('external_id') == external_id), None)
            if existing:
                if existing['song_id'] != song_id:
                    raise HTTPException(409, '此 Mureka 任務已連到另一首歌。')
                return existing
            try:
                result = self.api.poll(external_id)
            except MurekaApiError as exc:
                raise HTTPException(502, str(exc)) from exc
            if result['state'] != 'completed':
                raise HTTPException(409, '任務尚未完成或已失敗，暫不能匯入。')
            task = dict(id=str(uuid.uuid4()), song_id=song_id, title=song['title'],
                        snapshot=song, status='completed', external_id=external_id,
                        result=result, source='mureka-co', model='unknown', versions=len(result['audio_urls']),
                        message='已從 Mureka Co 匯入；未再次送件。', created_at=now())
            self.write(task)
            self.preserve_audio(task)
            rendered = song.setdefault('_mureka_versions', [])
            if not any(v.get('external_id') == external_id for v in rendered):
                rendered.append(dict(result, task_id=task['id'], created_at=now(), source='mureka-co'))
            song['_mureka_status'] = 'generated'
            song['_queued'] = False
            self.studio.save_song(song)
            return task

    def import_desktop_audio(self, song_id, filename, external_id=None):
        """Import an already generated Mureka Co file without another generation or API query."""
        if not isinstance(song_id, str) or not isinstance(filename, str):
            raise HTTPException(422, '請提供歌曲 ID 與桌面音檔名稱。')
        with self.lock:
            song = next((s for s in self.studio.songs() if s.get('_id') == song_id), None)
            if not song:
                raise HTTPException(404, '作品庫找不到這首歌。')
            expected = f"{song['title']}-0.mp3"
            if filename != expected or Path(filename).name != filename:
                raise HTTPException(422, '音檔名稱必須與作品標題相符。')
            if external_id is not None and not re.fullmatch(r'[0-9]{4,30}', str(external_id)):
                raise HTTPException(422, 'Mureka 任務 ID 格式不正確。')
            source_dir = Path.home() / 'Library/Application Support/mureka-desktop/generated-audio'
            source = source_dir / filename
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 60_000_000:
                raise HTTPException(404, '找不到有效的 Mureka Co 本機音檔。')
            existing = next((t for t in self.tasks() if t.get('song_id') == song_id and t.get('source') == 'mureka-co-local'), None)
            if existing:
                return existing
            local_id = str(external_id) if external_id else str(int(uuid.uuid4().int % 10**18)).zfill(18)
            if any(t.get('external_id') == local_id for t in self.tasks()):
                raise HTTPException(409, '此音檔 ID 已用於其他作品。')
            destination = self.audio_path(local_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix('.part')
            try:
                with source.open('rb') as source_file, temporary.open('wb') as target_file:
                    shutil.copyfileobj(source_file, target_file)
                if temporary.stat().st_size < 100_000:
                    raise ValueError('音檔過小')
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            result = {'state': 'completed', 'external_id': local_id, 'audio_urls': [], 'versions': []}
            task = dict(id=str(uuid.uuid4()), song_id=song_id, title=song['title'], snapshot=song,
                        status='completed', external_id=local_id, result=result, source='mureka-co-local',
                        model='desktop', versions=1, message='已從 Mureka Co 本機音檔匯入；未再次送件。',
                        created_at=now())
            self.write(task)
            song.setdefault('_mureka_versions', []).append(dict(result, task_id=task['id'], created_at=now(), source='mureka-co-local'))
            song['_mureka_status'] = 'generated'
            song['_queued'] = False
            self.studio.save_song(song)
            return task

    def resume(self, tid):
        with self.lock:
            task = self.task(tid)
            if task['status'] not in ('paused', 'awaiting_audio'):
                raise HTTPException(409, '這個工作不能安全續作；送件結果不明時請到 Mureka API 平台核對。')
            task.update(status='queued' if not task['external_id'] else 'polling', message='等待續作')
            self.write(task)
            self.pool.submit(self.run, tid)
            return task

    def run(self, tid):
        task = self.task(tid)
        if task['status'] not in ('queued', 'polling'):
            return
        try:
            if not task['external_id']:
                task.update(status='submitting', message='正在送交 Mureka；請勿再次送件。')
                self.write(task)
                try:
                    external_id = self.api.submit(task['snapshot'], task['model'], task['versions'])
                except Exception as exc:
                    # Timeout/parse failure could still mean a charged generation.
                    task.update(status='uncertain', message='送件結果不明；請到 Mureka API 平台核對後處理。',
                                error=str(exc)[:300])
                    self.write(task)
                    return
                task.update(status='polling', external_id=external_id, message='Mureka 正在製作')
                self.write(task)
            for _ in range(90):
                result = self.api.poll(task['external_id'])
                if result['state'] == 'completed':
                    task.update(status='completed', result=result, message='音樂已生成；請試聽並挑選版本。')
                    self.write(task)
                    self.preserve_audio(task)
                    with self.lock:
                        song = next((s for s in self.studio.songs() if s['_id'] == task['song_id']), None)
                        if song:
                            rendered = song.setdefault('_mureka_versions', [])
                            if not any(v.get('external_id') == task['external_id'] for v in rendered):
                                rendered.append(dict(result, task_id=tid, created_at=now()))
                            song['_mureka_status'] = 'generated'
                            song['_queued'] = False
                            self.studio.save_song(song)
                    return
                if result['state'] == 'failed':
                    task.update(status='failed', result=result, message='Mureka 回報製作失敗；請檢查帳號紀錄。')
                    self.write(task)
                    return
                self.wait(5)
            task.update(status='awaiting_audio', message='尚未取得音檔；可稍後繼續查詢。')
            self.write(task)
        except Exception as exc:
            task.update(status='awaiting_audio' if task['external_id'] else 'uncertain',
                        message='查詢中斷；可稍後繼續查詢。' if task['external_id'] else '送件結果不明，請至 Mureka 核對。',
                        error=str(exc)[:300])
            self.write(task)


def router(pipeline):
    api = APIRouter(prefix='/api/mureka-production')

    @api.get('/status')
    def status():
        return pipeline.status()

    @api.get('/tasks')
    def tasks():
        return pipeline.tasks()

    @api.get('/brief/{song_id}')
    def brief(song_id: str, model: str = 'auto'):
        song = next((s for s in pipeline.studio.songs() if s.get('_id') == song_id), None)
        if not song:
            raise HTTPException(404, '找不到歌曲')
        try:
            body = pipeline.api.payload(song, model, 1)
            desktop = co_prompt(song)
        except (MurekaApiError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {'title': song.get('title'), 'api_prompt': body['prompt'],
                'desktop_prompt': desktop, 'vocal_id': body.get('vocal_id'),
                'model': model, 'lyrics_length': len(body['lyrics'])}

    def saved_audio(external_id: str, disposition: str):
        if not re.fullmatch(r'[0-9]{4,30}', external_id):
            raise HTTPException(404, '找不到音檔')
        if not any(t.get('external_id') == external_id and t.get('status') == 'completed' for t in pipeline.tasks()):
            raise HTTPException(404, '找不到已完成的任務')
        path = pipeline.audio_path(external_id)
        if not path.is_file():
            raise HTTPException(404, '本機尚未保存此音檔')
        return FileResponse(path, media_type='audio/mpeg', filename=path.name, content_disposition_type=disposition)

    @api.get('/audio/{external_id}')
    def audio(external_id: str):
        return saved_audio(external_id, 'inline')

    @api.get('/audio/{external_id}/download')
    def download_audio(external_id: str):
        return saved_audio(external_id, 'attachment')

    @api.post('/submit')
    def submit(body: dict):
        ids = body.get('song_ids', [])
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(422, 'song_ids 必須是歌曲 ID 清單')
        return pipeline.submit(ids, body.get('model', 'auto'), body.get('versions', 1))

    @api.post('/import')
    def import_completed(body: dict):
        return pipeline.import_completed(body.get('song_id'), body.get('task_id'))

    @api.post('/import-desktop-audio')
    def import_desktop_audio(body: dict):
        return pipeline.import_desktop_audio(body.get('song_id'), body.get('filename'), body.get('task_id'))

    @api.post('/tasks/{tid}/resume')
    def resume(tid: str):
        return pipeline.resume(tid)

    return api
