import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from production import Studio
from mureka_api import MurekaApi, MurekaApiError
from mureka_pipeline import MurekaPipeline


class Inline:
    def submit(self, fn, *args):
        fn(*args)


class FakeOfficial:
    config_error = None

    def __init__(self):
        self.submissions = []

    def verify(self):
        return True

    def submit(self, song, model, versions):
        self.submissions.append(song['title'])
        return 'official-task-1'

    def poll(self, external_id):
        return {'state': 'completed', 'external_id': external_id,
                'audio_urls': ['https://audio.mureka.ai/song.mp3']}

    def payload(self, song, model, versions):
        return MurekaApi.payload(song, model, versions)


@pytest.fixture
def setup(tmp_path):
    studio = Studio(tmp_path / 'studio.sqlite3', lambda x: [], lambda *x: {}, lambda *x: {})
    studio.pool.shutdown()
    client = FakeOfficial()
    pipeline = MurekaPipeline(studio, client)
    pipeline.pool.shutdown()
    pipeline.pool = Inline()
    return studio, pipeline, client


def test_selected_song_submits_once_and_returns_audio(setup):
    studio, pipeline, client = setup
    song = studio.save_song({'title': 'original', 'style': 'city pop', 'lyrics': 'hello', '_queued': True})
    task = pipeline.submit([song['_id']])[0]
    assert pipeline.task(task['id'])['status'] == 'completed'
    assert client.submissions == ['original']
    assert studio.songs()[0]['_mureka_versions'][0]['audio_urls'] == ['https://audio.mureka.ai/song.mp3']
    with pytest.raises(HTTPException) as exc:
        pipeline.submit([song['_id']])
    assert exc.value.status_code == 409


def test_unselected_song_cannot_consume_credits(setup):
    studio, pipeline, client = setup
    song = studio.save_song({'title': 'draft', 'style': 'jazz', 'lyrics': 'x', '_queued': False})
    with pytest.raises(HTTPException) as exc:
        pipeline.submit([song['_id']])
    assert exc.value.status_code == 422
    assert not client.submissions


def test_import_existing_mureka_co_task_without_generation(setup):
    studio, pipeline, client = setup
    song = studio.save_song({'title': 'from desktop', 'style': 'R&B', 'lyrics': 'hello'})
    task = pipeline.import_completed(song['_id'], '162665945169921')
    assert task['status'] == 'completed'
    assert task['source'] == 'mureka-co'
    assert not client.submissions
    assert pipeline.import_completed(song['_id'], '162665945169921')['id'] == task['id']
    assert len(studio.songs()[0]['_mureka_versions']) == 1


def test_saved_audio_is_preferred_in_task_listing(setup):
    studio, pipeline, client = setup
    song = studio.save_song({'title': 'saved', 'style': 'R&B', 'lyrics': 'hello'})
    pipeline.import_completed(song['_id'], '162665945169921')
    path = pipeline.audio_path('162665945169921')
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b'ID3' + b'\0' * 2048)
    assert pipeline.tasks()[0]['local_audio_url'] == '/api/mureka-production/audio/162665945169921'
    assert not client.submissions


def test_import_desktop_audio_does_not_call_generation_or_query(setup, tmp_path, monkeypatch):
    studio, pipeline, client = setup
    song = studio.save_song({'title': 'local take', 'style': 'R&B', 'lyrics': 'hello', '_queued': True})
    source_dir = tmp_path / 'Library/Application Support/mureka-desktop/generated-audio'
    source_dir.mkdir(parents=True)
    (source_dir / 'local take-0.mp3').write_bytes(b'ID3' + b'\0' * 150_000)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    client.poll = lambda *_: pytest.fail('desktop import must not query the API')
    task = pipeline.import_desktop_audio(song['_id'], 'local take-0.mp3', '123456789012')
    assert task['status'] == 'completed'
    assert pipeline.audio_path('123456789012').is_file()
    assert pipeline.tasks()[0]['local_audio_url'].endswith('/123456789012')
    assert not studio.songs()[0]['_queued']
    assert not client.submissions
    assert pipeline.import_desktop_audio(song['_id'], 'local take-0.mp3', '123456789012')['id'] == task['id']


def test_uncertain_submission_never_retries_automatically(setup):
    studio, pipeline, client = setup
    song = studio.save_song({'title': 'maybe charged', 'style': 'jazz', 'lyrics': 'x', '_queued': True})
    def ambiguous(song, model, versions):
        client.submissions.append(song['title'])
        raise MurekaApiError('timeout')
    client.submit = ambiguous
    task = pipeline.submit([song['_id']])[0]
    assert pipeline.task(task['id'])['status'] == 'uncertain'
    with pytest.raises(HTTPException):
        pipeline.resume(task['id'])
    assert len(client.submissions) == 1


def test_recovery_preserves_uncertain_and_can_resume_polling(setup):
    studio, pipeline, client = setup
    pipeline.write({'id': 'a', 'song_id': 's', 'title': 'a', 'snapshot': {},
                    'status': 'submitting', 'external_id': None})
    pipeline.write({'id': 'b', 'song_id': 's', 'title': 'b', 'snapshot': {},
                    'status': 'polling', 'external_id': 'known'})
    restored = MurekaPipeline(studio, client)
    restored.pool.shutdown()
    restored.pool = Inline()
    assert restored.task('a')['status'] == 'uncertain'
    assert restored.task('b')['status'] == 'paused'
    restored.resume('b')
    assert restored.task('b')['status'] == 'completed'
    assert not client.submissions


def test_adapter_missing_key_disables_submission():
    api = MurekaApi(key='', key_file='/nonexistent/key')
    assert api.config_error and 'MUREKA_API_KEY' in api.config_error
    with pytest.raises(MurekaApiError):
        api.submit({'lyrics': 'hello', 'style': 'jazz'})


def test_adapter_reads_private_local_key_without_exposing_it(tmp_path, monkeypatch):
    monkeypatch.delenv('MUREKA_API_KEY', raising=False)
    path = tmp_path / '.mureka_api_key'
    path.write_text('private-test-key')
    path.chmod(0o600)
    api = MurekaApi(key_file=path)
    assert api.config_error is None
    assert api.key == 'private-test-key'
    path.chmod(0o644)
    unsafe = MurekaApi(key_file=path)
    assert unsafe.config_error and '權限' in unsafe.config_error
    assert 'private-test-key' not in unsafe.config_error


def test_adapter_uses_official_fields_and_auth_header():
    calls = []
    class Response:
        def __init__(self, value, url): self.value, self.url = value, url
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return self.url
        def read(self, amount): return json.dumps(self.value).encode()
    def opener(request, timeout):
        calls.append(request)
        return Response({'id': 'abc'} if request.method == 'POST' else
                        {'status': 'succeeded', 'choices': [{'id': 'v1', 'url': 'https://cdn.mureka.ai/a.mp3'}]}, request.full_url)
    api = MurekaApi(key='secret', opener=opener)
    assert api.config_error is None
    assert api.submit({'lyrics': 'line', 'style': 'R&B', 'vocal_gender': 'female', 'title': 'local only'}) == 'abc'
    assert calls[0].full_url == 'https://api.mureka.ai/v1/song/generate'
    sent = json.loads(calls[0].data)
    assert {k: sent[k] for k in ('lyrics', 'model', 'n', 'gender')} == {'lyrics': 'line', 'model': 'auto', 'n': 1, 'gender': 'female'}
    assert sent['prompt'].startswith('R&B. Consistent female lead vocal')
    assert 'Arrangement:' in sent['prompt']
    assert calls[0].get_header('Authorization') == 'Bearer secret'
    assert api.poll('abc')['audio_urls'] == ['https://cdn.mureka.ai/a.mp3']


def test_adapter_rejects_overlong_input_and_bad_versions():
    song = {'lyrics': 'x', 'style': 'jazz'}
    with pytest.raises(MurekaApiError):
        MurekaApi.payload(song, n=4)
    with pytest.raises(MurekaApiError):
        MurekaApi.payload({**song, 'lyrics': 'x' * 5001})


def test_brief_keeps_voice_profile_and_mureka_id():
    song = {'title': '夜裡的光', 'style': 'Chinese neo-soul, 86 BPM', 'lyrics': '[Verse]\n今晚不怕',
            'bpm': '86', 'vocal_gender': 'female', '_payload': {'language': '中文+English', 'instruments': 'Rhodes, guzheng'},
            '_mureka_voice_profile': 'warm low female alto, intimate, clear Mandarin and English diction',
            '_mureka_notes': 'memorable three-note chorus hook', '_mureka_vocal_id': 'voice_123'}
    body = MurekaApi.payload(song)
    assert body['vocal_id'] == 'voice_123'
    assert body['gender'] == 'female'
    assert 'Lead vocal:' in body['prompt']
    assert 'memorable three-note chorus hook' in body['prompt']
    assert 'Language and diction: 中文+English' in body['prompt']
    assert body['prompt'].count('86 BPM') == 1
    assert len(body['prompt']) <= 1024
    with pytest.raises(MurekaApiError):
        MurekaApi.payload(song, model='mureka-o2')


def test_brief_rejects_oversized_important_parts():
    with pytest.raises(MurekaApiError):
        MurekaApi.payload({'lyrics': 'hello', 'style': 'a' * 1020,
                           '_mureka_voice_profile': 'same singer'})
