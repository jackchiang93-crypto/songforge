"""Compile an editable song into one focused Mureka production direction."""
import re


MAX_PROMPT = 1024


def _clean(value, limit):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if len(text) > limit:
        raise ValueError(f'製作欄位超過 {limit} 字，請精簡後再送件。')
    return text


def compile_brief(song):
    style = _clean(song.get('style'), MAX_PROMPT)
    if not style:
        raise ValueError('請先填入曲風。')
    settings = song.get('_payload') or {}
    voice = _clean(song.get('_mureka_voice_profile'), 300)
    notes = _clean(song.get('_mureka_notes'), 300)
    avoid = _clean(song.get('exclude_styles'), 180)
    language = _clean(settings.get('language'), 80)
    bpm = _clean(song.get('bpm'), 20)
    musical_key = _clean(song.get('musical_key'), 40)
    instruments = _clean(settings.get('instruments'), 150)
    song_brief = song.get('_brief') if isinstance(song.get('_brief'), dict) else {}
    hook = _clean(song_brief.get('hook_idea'), 220)
    arrangement = _clean(song_brief.get('arrangement'), 220)
    gender = str(song.get('vocal_gender') or '').lower()

    # The source style is kept intact. Optional detail is added in priority
    # order, never silently cutting a user-authored style or voice profile.
    parts = [style.rstrip(' ,;').rstrip('.') + '.']
    if voice:
        parts.append('Lead vocal: ' + voice.rstrip('.') + '.')
    elif gender in ('male', 'female'):
        parts.append(f'Consistent {gender} lead vocal; natural expressive phrasing.')
    if notes:
        parts.append('Song-specific production direction: ' + notes)
    candidates = [
        f'Chorus hook: {hook}; shape it into a short singable motif.' if hook else '',
        f'Arrangement detail: {arrangement}.' if arrangement else '',
        f'Language and diction: {language}; pronounce every word clearly and switch languages naturally.' if language else '',
        f'Featured instruments: {instruments}; leave space for the lead vocal.' if instruments else '',
        f'Tempo: {bpm} BPM.' if bpm and re.fullmatch(r'\d{2,3}', bpm) and not re.search(r'\b' + re.escape(bpm) + r'\s*bpm\b', style, re.I) else '',
        f'Tonal center: {musical_key}.' if musical_key else '',
        'Arrangement: intimate verses, memorable lifted chorus, contrasting bridge, uncluttered final chorus.' if not arrangement else '',
        'Production: clear lead vocal, defined groove and bass, instrument separation, controlled dynamics; avoid harsh or muddy mix.',
        f'Avoid: {avoid}.' if avoid else '',
    ]
    prompt = ' '.join(parts)
    if len(prompt) > MAX_PROMPT:
        raise ValueError('曲風、人聲定位與單曲指令合計超過 1024 字，請精簡。')
    for item in candidates:
        if item and len(prompt) + len(item) + 1 <= MAX_PROMPT:
            prompt += ' ' + item
    return prompt


def co_prompt(song):
    """Copyable desktop-app message; separate from the API's prompt field."""
    title = _clean(song.get('title'), 100)
    return (f'Create one original song titled "{title}".\n'
            f'Music direction: {compile_brief(song)}\n'
            'Use the lyrics below in this section order. Keep the hook clear and the vocal intelligible. '
            'Do not copy an existing song or artist voice.\n\n'
            f'{str(song.get("lyrics") or "").strip()}')
