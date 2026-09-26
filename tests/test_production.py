import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from production import Studio, router


REVIEW = dict(naturalness=85, singability=83, hook=90, story=81, style_fit=90,
              issues=[], revision_direction='可進行人工審閱')


@pytest.fixture
def studio(tmp_path):
    service = Studio(tmp_path / 'test.sqlite3', lambda req: [{'concept': str(i)} for i in range(req['count'])],
                     lambda req, brief, history: {'title': brief['concept'], 'style': 'jazz', 'lyrics': 'test'},
                     lambda *args: REVIEW)
    # Execute synchronously for deterministic recovery tests, without model/network usage.
    service.pool.shutdown()
    class Inline:
        def submit(self, fn, *args):
            fn(*args)
    service.pool = Inline()
    yield service


def test_jobs_persist_and_failed_retry_does_not_regenerate_success(studio):
    calls = []
    def generate(req, brief, history):
        calls.append(brief['concept'])
        if brief['concept'] == '1' and calls.count('1') == 1:
            raise RuntimeError('temporary issue')
        return dict(title=brief['concept'], style='jazz', lyrics='hello')
    studio.generate = generate
    job = studio.start({'count': 2})
    assert studio.job(job['id'])['status'] == 'partial'
    studio.retry(job['id'])
    assert studio.job(job['id'])['status'] == 'completed'
    assert calls == ['0', '1', '1']
    assert len(studio.songs()) == 2


def test_restart_marks_job_interrupted_and_recovers_committed_song(studio):
    studio.write_job(dict(id='resume', status='running', request={'count': 2}, items=[
        dict(brief={'concept': '0'}, song_id='saved', status='reviewing'),
        dict(brief={'concept': '1'}, song_id='next', status='pending')]))
    studio.save_song(dict(_id='saved', title='0', style='jazz', lyrics='draft'))
    restored = Studio(studio.path, studio.plan, studio.generate, studio.review_model)
    restored.pool.shutdown()
    restored.pool = studio.pool
    assert restored.job('resume')['status'] == 'interrupted'
    restored.retry('resume')
    assert len(restored.songs()) == 2
    assert restored.job('resume')['status'] == 'completed'


def test_deleted_song_cannot_be_revived_by_late_browser_save(studio):
    song = studio.save_song(dict(title='x', style='jazz', lyrics='x'))
    with studio.db() as db:
        db.execute('UPDATE songs SET deleted=1 WHERE id=?', (song['_id'],))
    with pytest.raises(HTTPException) as exc:
        studio.save_song(song)
    assert exc.value.status_code == 409
    assert not studio.songs()


def test_failed_review_preserves_draft_as_unreviewed(studio):
    studio.review_model = lambda *args: {'invalid': True}
    job = studio.start({'count': 1})
    assert studio.job(job['id'])['status'] == 'completed'
    assert studio.songs()[0]['_review']['status'] == 'unreviewed'


def test_quality_gate_respects_weak_dimension_and_technical_warnings(studio):
    studio.review_model = lambda *args: dict(REVIEW, singability=40)
    assert studio.assess(dict(title='x', lyrics='x'), {})['status'] == 'needs_revision'
    studio.review_model = lambda *args: REVIEW
    studio.check = lambda song: ['人聲設定衝突']
    assert studio.assess(dict(title='x', lyrics='x'), {})['status'] == 'needs_revision'


def test_cancellation_preserves_current_song_and_stops_next(studio):
    def generate(req, brief, history):
        job = studio.jobs()[0]
        job['status'] = 'cancelling'
        studio.write_job(job)
        return dict(title='first', lyrics='x', style='jazz')
    studio.generate = generate
    job = studio.start({'count': 3})
    assert studio.job(job['id'])['status'] == 'cancelled'
    assert len(studio.songs()) == 1


def test_persisted_input_is_a_snapshot(studio):
    source = {'count': 1, 'settings': {'language': '中文+English'}}
    job = studio.start(source)
    source['settings']['language'] = '日本語'
    assert studio.job(job['id'])['request']['settings']['language'] == '中文+English'


def test_revision_keeps_original_and_controls_but_not_old_approval(studio):
    source = studio.save_song(dict(title='original', style='jazz', lyrics='old', weirdness=17,
                                    exclude_styles='metal', vocal_gender='female', _queued=True,
                                    _suno_url='old-render', _review=REVIEW))
    studio.revise = lambda song: dict(song, lyrics='new')
    app = FastAPI()
    app.include_router(router(studio, lambda x: x))
    response = TestClient(app).post('/api/studio/songs/'+source['_id']+'/revise')
    assert response.status_code == 200
    revised = response.json()
    assert revised['_parent_id'] == source['_id'] and revised['_id'] != source['_id']
    assert revised['weirdness'] == 17 and revised['exclude_styles'] == 'metal'
    assert revised['vocal_gender'] == 'female' and not revised['_queued']
    assert '_suno_url' not in revised
    assert next(s for s in studio.songs() if s['_id'] == source['_id'])['lyrics'] == 'old'


def test_suno_status_never_claims_connected(studio):
    app = FastAPI()
    app.include_router(router(studio, lambda x: x))
    assert TestClient(app).get('/api/studio/suno-status').json()['connected'] is False


def test_unavailable_model_stops_remaining_requests(studio):
    calls = []
    def unavailable(*args):
        calls.append(1)
        raise HTTPException(503, 'quota unavailable')
    studio.generate = unavailable
    job = studio.start({'count': 10})
    assert len(calls) == 1
    assert studio.job(job['id'])['status'] == 'failed'
    assert sum(i['status'] == 'pending' for i in studio.job(job['id'])['items']) == 9
