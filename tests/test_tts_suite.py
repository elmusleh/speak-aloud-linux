#!/usr/bin/env python3
"""
Automated unit & integration test suite for Speak a Loud Universal.
Covers core text processing, trilingual segmentation, fast first-chunk splitting,
timeline math, queue CLI, per-user 0700 runtime directory, schema-validated atomic
settings store, pronunciation lexicon, async synthesis worker & Piper model discovery,
OCR image extraction, live transcript drawer & HUD hotkeys, and systemd service unit.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import tts_ocr
import tts_synth_worker as sw
import tts_text_processor as tp
from tts_hud import MPVTimelineController, format_time


def test_clean_and_filter_markdown_and_symbols():
    raw = """---
title: Test Note
tags: [tts]
---
# Section 1: Overview
Check [Documentation](https://example.com/docs) at https://raw.url/test.
• Frame size: 320 x 240 >= 100 and <= 500
• **Important**: Latency ≈ 12ms"""

    cleaned = tp.clean_and_filter_text(
        raw,
        filter_markdown=True,
        filter_urls=True,
        filter_code=False,
        replace_symbols=True,
    )
    assert 'title: Test Note' not in cleaned
    assert 'https://example.com' not in cleaned
    assert 'https://raw.url' not in cleaned
    assert 'Documentation' in cleaned
    assert '320 by 240' in cleaned
    assert 'greater than or equal to' in cleaned
    assert 'less than or equal to' in cleaned
    assert 'approximately 12ms' in cleaned


def test_trilingual_segmentation_and_technical_jargon():
    sample = (
        "Single Grayscale Frame: 320 by 240. "
        "Intermediate / Filtering Buffer (e.g., Gaussian blur, Sobel gradient): 150 KB. "
        "مرحبا بكم في النظام الجديد 2026. "
        "Das System läuft äußerst zuverlässig und schnell."
    )
    segs = tp.segment_by_language(sample, source_lang='auto')
    langs = [lang for lang, _ in segs]
    assert langs == ['en', 'ar', 'de']
    assert '2026' in segs[1][1]


def test_fast_first_chunk_and_manifest():
    long_en = " ".join(
        [f"Sentence number {i} explains how progressive neural audio streaming works with minimal latency." for i in range(1, 12)]
    )
    with tempfile.TemporaryDirectory() as d:
        count = tp.process_and_write_segments(
            long_en,
            workdir=d,
            first_chunk_max=220,
            max_segment_chars=480,
        )
        assert count >= 2
        with open(os.path.join(d, 'manifest.json'), 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        assert manifest['total_chunks'] == count
        assert manifest['chunks'][0]['chars'] <= 220
        for c in manifest['chunks'][1:]:
            assert c['chars'] <= 480


def test_timeline_controller_segment_math():
    ctrl = MPVTimelineController()
    ctrl.total_chunks = 3
    ctrl.chunk_durations = [10.0, 20.0, 15.0]
    ctrl.total_duration = 45.0

    seg, off = ctrl._target_segment_and_offset(5.5)
    assert seg == 0 and abs(off - 5.5) < 1e-4

    seg, off = ctrl._target_segment_and_offset(18.0)
    assert seg == 1 and abs(off - 8.0) < 1e-4

    seg, off = ctrl._target_segment_and_offset(35.0)
    assert seg == 2 and abs(off - 5.0) < 1e-4


def test_format_time():
    assert format_time(0) == '0:00'
    assert format_time(65.2) == '1:05'
    assert format_time(125.9) == '2:06'


def test_queue_cli_mode():
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env['TMPDIR'] = tmp
        env['XDG_RUNTIME_DIR'] = tmp
        r = subprocess.run(
            ['bash', os.path.join(SCRIPT_DIR, 'speak.sh'), '--queue', '--text', 'First queued passage for testing.'],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r.returncode == 0
        qfile = os.path.join(tmp, 'speak-aloud-queue.json')
        assert os.path.exists(qfile)
        with open(qfile, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert len(data['items']) == 1
        assert 'First queued passage' in data['items'][0]['text']


def test_per_user_runtime_dir_and_permissions():
    rdir = tp.get_runtime_dir()
    assert os.path.isdir(rdir)
    assert f'speak-aloud-{os.getuid()}' in rdir
    mode = stat.S_IMODE(os.stat(rdir).st_mode)
    assert mode == 0o700


def test_schema_validation_and_atomic_settings_store():
    with tempfile.TemporaryDirectory() as cdir:
        store = tp.TTSSettingsStore(config_dir=cdir)
        saved = store.save({
            'rate_en': '+999%',
            'rate_ar': '-99%',
            'global_speed': '9.5',
            'pause_punctuation': 'TRUE',
            'skip_code': 'FALSE',
            'pause_delay': '5000',
            'translate_provider': 'invalid_provider',
        })
        assert saved['rate_en'] == '+100%'
        assert saved['rate_ar'] == '-50%'
        assert saved['global_speed'] == '3.0'
        assert saved['pause_punctuation'] == 'yes'
        assert saved['skip_code'] == 'no'
        assert saved['pause_delay'] == '2000'
        assert saved['translate_provider'] == 'google'

        # Verify atomic JSON and flat file creation with 0600 permissions
        json_path = os.path.join(cdir, 'settings.json')
        assert os.path.isfile(json_path)
        assert stat.S_IMODE(os.stat(json_path).st_mode) == 0o600
        loaded = store.load()
        assert loaded['rate_en'] == '+100%'
        assert loaded['pause_punctuation'] == 'yes'


def test_pronunciation_lexicon_replacement():
    with tempfile.TemporaryDirectory() as cdir:
        lex = tp.save_lexicon({'kubectl': 'kube control', 'PyQt6': 'Pie Cute 6', 'nginx': 'engine X'}, config_dir=cdir)
        cleaned = tp.clean_and_filter_text(
            'Run kubectl and nginx with PyQt6 GUI.',
            use_lexicon=True,
            lexicon=lex,
        )
        assert 'kube control' in cleaned
        assert 'engine X' in cleaned
        assert 'Pie Cute 6' in cleaned


def test_async_synth_worker_and_piper_discovery():
    assert sw._ensure_edge_tts_importable() is True
    with tempfile.TemporaryDirectory() as mdir:
        fake_onnx = os.path.join(mdir, 'en_US-lessac-medium.onnx')
        with open(fake_onnx, 'wb') as f:
            f.write(b'ONNX_MODEL_HEADER')
        resolved = sw.resolve_piper_model(lang='en', configured_model=fake_onnx)
        assert resolved == fake_onnx


def test_ocr_image_extraction():
    from PIL import Image, ImageDraw, ImageFont

    with tempfile.TemporaryDirectory() as tmp:
        img_path = os.path.join(tmp, 'ocr_sample.png')
        img = Image.new('RGB', (640, 140), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
        except Exception:
            font = ImageFont.load_default()
        draw.text((28, 45), 'Speak Aloud OCR 2026', fill=(0, 0, 0), font=font)
        img.save(img_path, 'PNG')

        extracted = tts_ocr.extract_text_from_image(img_path, langs='eng')
        assert 'Speak' in extracted
        assert '2026' in extracted


def test_hud_transcript_and_hotkeys():
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QApplication
    from tts_hud import FloatingPlayerWindow, QueueManagerWindow

    _app = QApplication.instance() or QApplication([])
    ctrl = MPVTimelineController()
    ctrl.chunks_meta = [
        {'id': 0, 'lang': 'en', 'text': 'First sentence in transcript.'},
        {'id': 1, 'lang': 'de', 'text': 'Zweiter Satz im Transkript.'},
    ]
    ctrl.chunk_durations = [4.0, 6.0]
    ctrl.total_duration = 10.0

    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Toggle transcript via hotkey 'T'
    assert hud.transcript_box.isHidden() is True
    ev_t = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_T, Qt.KeyboardModifier.NoModifier)
    hud.keyPressEvent(ev_t)
    assert hud.transcript_box.isHidden() is False
    assert len(hud._transcript_rows) == 2

    # Step speed up via ']'
    initial_spd = ctrl.speed
    ev_up = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier)
    hud.keyPressEvent(ev_up)
    assert ctrl.speed >= initial_spd


def test_systemd_unit_file_valid():
    unit_path = os.path.join(SCRIPT_DIR, 'speak-aloud-daemon.service')
    assert os.path.isfile(unit_path)
    with open(unit_path, 'r', encoding='utf-8') as f:
        content = f.read()
    assert '[Unit]' in content
    assert '[Service]' in content
    assert 'Restart=on-failure' in content
    assert 'tts-daemon.py' in content


def test_config_newline_safety_and_legacy_sync(tmp_path):
    from tts_text_processor import TTSSettingsStore, _format_rate_str
    store = TTSSettingsStore(config_dir=str(tmp_path))
    saved = store.save({'rate_en': '+45%', 'voice_en': 'en-US-ChristopherNeural', 'translate_from': 'auto'})
    assert saved['rate_en'] == '+45%'

    # Ensure all files end with newline so bash head/cat never concatenates adjacent tokens
    rate_file = tmp_path / 'rate_en'
    assert rate_file.read_bytes().endswith(b'\n')
    assert rate_file.read_text().strip() == '+45%'

    legacy_rate = tmp_path / 'rate'
    assert legacy_rate.read_bytes().endswith(b'\n')
    assert legacy_rate.read_text().strip() == '+45%'

    # Test edge-tts rate string normalization
    assert _format_rate_str('+45%') == '+45%'
    assert _format_rate_str('45') == '+45%'
    assert _format_rate_str('-20%') == '-20%'
    assert _format_rate_str('+0%+50%') in ('+0%', '+50%')  # Recovers to valid edge-tts rate


def test_mpris_service_registration_and_properties():
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_mpris import MPRISServer
    class DummyDaemon:
        def __init__(self):
            self._state = 'PLAYING'
            self.controller = None
    daemon = DummyDaemon()
    server = MPRISServer(daemon, service_name='org.mpris.MediaPlayer2.SpeakALoudTestUnit')
    assert server.registered is True
    assert server._root_adaptor.Identity == 'Speak a Loud'
    assert server._root_adaptor.CanQuit is True
    assert server._player_adaptor.PlaybackStatus == 'Playing'


def test_word_boundary_karaoke_highlighting(tmp_path):
    from PyQt6.QtWidgets import QApplication, QLabel
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow
    ctrl = MPVTimelineController()
    ctrl.session_id = 'test_karaoke_session'
    ctrl.current_time = 0.5
    ctrl.total_duration = 5.0
    ctrl.chunks_meta = [{'lang': 'en', 'text': 'Hello world test'}]
    ctrl.chunk_durations = [5.0]

    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)
    hud._sync_transcript(ctrl.session_id, ctrl.chunks_meta, 0)
    assert len(hud._transcript_rows) == 1

    # Inject mock word boundary metadata
    words = [
        {'word': 'Hello', 'start': 0.0, 'end': 0.4},
        {'word': 'world', 'start': 0.41, 'end': 0.8},
        {'word': 'test', 'start': 0.81, 'end': 1.2},
    ]
    hud._segment_words[0] = words

    # Run sync at t=0.5s -> 'world' should be highlighted with background-color #4F46E5
    hud._sync_transcript(ctrl.session_id, ctrl.chunks_meta, 0)
    lbl = hud._transcript_rows[0].findChild(QLabel)
    assert lbl is not None
    assert 'world' in lbl.text()
    assert '#4F46E5' in lbl.text()


def test_audio_ducker_sink_discovery_and_interface():
    from tts_ducking import AudioDucker
    ducker = AudioDucker(duck_volume_percent=40)
    assert ducker.duck_volume_percent == 40
    # Safe methods do not throw
    streams = ducker.get_other_sink_inputs()
    assert isinstance(streams, list)


def test_keyring_integration_helpers(monkeypatch):
    import subprocess
    from tts_text_processor import get_keyring_api_key, store_keyring_api_key

    # Mock subprocess run for secret-tool
    def mock_run(cmd, *args, **kwargs):
        class MockRes:
            returncode = 0
            stdout = 'sk-mock-api-key-12345\n'
        return MockRes()

    monkeypatch.setattr(subprocess, 'run', mock_run)
    monkeypatch.setattr('shutil.which', lambda name: '/usr/bin/secret-tool' if name == 'secret-tool' else None)

    assert get_keyring_api_key() == 'sk-mock-api-key-12345'
    assert store_keyring_api_key('new-key') is True


def test_document_and_web_reader_extraction(tmp_path):
    from tts_reader import SimpleHTMLTextExtractor, enqueue_documents

    html_sample = """
    <html>
      <head><title>Sample Article Title</title></head>
      <body>
        <nav><a href="/">Home</a></nav>
        <h1>Main Heading</h1>
        <p>This is the first paragraph with critical insights.</p>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    parser = SimpleHTMLTextExtractor()
    parser.feed(html_sample)
    assert parser.title == 'Sample Article Title'
    text = parser.get_text()
    assert 'Main Heading' in text
    assert 'critical insights' in text
    assert 'Copyright' not in text  # Footer stripped

    # Test enqueue documents
    docs = [{'title': 'Chapter 1', 'text': 'Clean enqueued paragraph.'}]
    count = enqueue_documents(docs, source='test')
    assert count == 1


def test_bookmark_saving_and_hotkey(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import Qt

    b_dir = tmp_path / 'speak-aloud'
    monkeypatch.setattr('os.path.expanduser', lambda p: str(b_dir) if 'speak-aloud' in p else os.path.expanduser(p))

    ctrl = MPVTimelineController()
    ctrl.current_time = 15.0
    ctrl.total_duration = 60.0
    ctrl.chunks_meta = [{'text': 'A memorable technical sentence to bookmark.'}]
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Press hotkey 'B'
    ev_b = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_B, Qt.KeyboardModifier.NoModifier)
    hud.keyPressEvent(ev_b)

    bfile = b_dir / 'bookmarks.md'
    assert bfile.exists()
    content = bfile.read_text(encoding='utf-8')
    assert 'A memorable technical sentence to bookmark.' in content
    assert '0:15 / 1:00' in content


def test_system_theme_detection_and_popup_styling(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_text_processor import get_effective_theme, TTSSettingsStore
    from tts_hud import (
        FloatingPlayerWindow,
        MPVTimelineController,
        QueueManagerWindow,
        get_player_hud_stylesheet,
        get_queue_stylesheet,
    )

    # 1. Test explicit configuration overrides
    cfg_dir = str(tmp_path / 'cfg')
    os.makedirs(cfg_dir, exist_ok=True)
    monkeypatch.setattr('tts_text_processor.CONFIG_DIR', cfg_dir)

    with open(os.path.join(cfg_dir, 'ui_theme'), 'w') as f:
        f.write('light\n')
    assert get_effective_theme(max_age_sec=0) == 'light'

    with open(os.path.join(cfg_dir, 'ui_theme'), 'w') as f:
        f.write('dark\n')
    assert get_effective_theme(max_age_sec=0) == 'dark'

    # 2. Test auto detection default
    with open(os.path.join(cfg_dir, 'ui_theme'), 'w') as f:
        f.write('auto\n')
    eff = get_effective_theme(app=_app, max_age_sec=0)
    assert eff in ('dark', 'light')

    # 3. Test FloatingPlayerWindow and QueueManagerWindow theme switching
    ctrl = MPVTimelineController()
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Apply Light Theme
    hud.apply_theme('light')
    qwin.apply_theme('light')
    assert hud._current_theme == 'light'
    assert qwin._current_theme == 'light'
    assert '#FFFFFF' in hud.styleSheet()
    assert '#FFFFFF' in qwin.styleSheet()

    # Apply Dark Theme
    hud.apply_theme('dark')
    qwin.apply_theme('dark')
    assert hud._current_theme == 'dark'
    assert qwin._current_theme == 'dark'
    assert '#16181E' in hud.styleSheet()
    assert '#16181E' in qwin.styleSheet()


def test_audio_cache_hash_and_lru_pruning(tmp_path, monkeypatch):
    import time
    from tts_text_processor import (
        get_audio_cache_path,
        prune_audio_cache,
        prune_runtime_dir,
    )

    cdir = str(tmp_path / 'cache_audio')
    monkeypatch.setattr('tts_text_processor.AUDIO_CACHE_DIR', cdir)
    os.makedirs(cdir, exist_ok=True)

    # 1. Deterministic Hash Path
    p1 = get_audio_cache_path("Sample text to cache", "en-US-ChristopherNeural", "+10%", "+5Hz")
    p2 = get_audio_cache_path("Sample text to cache", "en-US-ChristopherNeural", "+10%", "+5Hz")
    p3 = get_audio_cache_path("Different text", "en-US-ChristopherNeural", "+10%", "+5Hz")
    assert p1 == p2
    assert p1 != p3
    assert p1.endswith('.mp3')

    # 2. Write dummy cached mp3 files
    f_old = os.path.join(cdir, 'old.mp3')
    with open(f_old, 'wb') as f:
        f.write(b'\x00' * 500)
    # Set old timestamp (15 days ago)
    old_time = time.time() - (15 * 86400)
    os.utime(f_old, (old_time, old_time))

    f_new = os.path.join(cdir, 'new.mp3')
    with open(f_new, 'wb') as f:
        f.write(b'\x00' * 500)

    res = prune_audio_cache(max_size_mb=10, max_age_days=14)
    assert res['deleted_files'] >= 1
    assert not os.path.exists(f_old)
    assert os.path.exists(f_new)

    # 3. Test Runtime Directory Pruner
    rdir = str(tmp_path / 'runtime')
    os.makedirs(rdir, exist_ok=True)
    monkeypatch.setattr('tts_text_processor.get_runtime_dir', lambda: rdir)
    old_session = os.path.join(rdir, 'work_old_session')
    os.makedirs(old_session, exist_ok=True)
    os.utime(old_session, (time.time() - 100_000, time.time() - 100_000))
    pruned = prune_runtime_dir(max_age_hours=24)
    assert pruned >= 1
    assert not os.path.exists(old_session)


def test_speech_pitch_formatting_and_schema():
    from tts_text_processor import format_pitch_str, TTSSettingsStore

    assert format_pitch_str('+5Hz') == '+5Hz'
    assert format_pitch_str('-12Hz') == '-12Hz'
    assert format_pitch_str('0') == '+0Hz'
    assert format_pitch_str(None) == '+0Hz'
    assert format_pitch_str('invalid_value') == '+0Hz'
    assert format_pitch_str('+90Hz') == '+50Hz'  # Clamped to 50

    val = TTSSettingsStore.validate({'pitch': '-8Hz', 'obsidian_vault': 'My Vault'})
    assert val['pitch'] == '-8Hz'
    assert val['obsidian_vault'] == 'My Vault'


def test_subprocess_environment_sanitization(monkeypatch):
    from tts_text_processor import get_sanitized_env

    monkeypatch.setenv('LD_PRELOAD', '/malicious/lib.so')
    monkeypatch.setenv('PYTHONPATH', '/custom/path')
    monkeypatch.setenv('DISPLAY', ':0')

    clean = get_sanitized_env({'CUSTOM_VAR': 'safe'})
    assert 'LD_PRELOAD' not in clean
    assert 'PYTHONPATH' not in clean
    assert clean.get('DISPLAY') == ':0'
    assert clean.get('CUSTOM_VAR') == 'safe'
    assert '/usr/bin' in clean.get('PATH', '')


def test_sleep_timer_state_and_hud_controls():
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import Qt

    ctrl = MPVTimelineController()
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Initial state
    assert hud.sleep_timer_seconds == 0
    assert hud.sleep_timer_mode == 0

    # Cycle once (0 -> 15m)
    hud._cycle_sleep_timer()
    assert hud.sleep_timer_seconds == 15 * 60
    assert hud.sleep_timer_mode == 1
    assert '15:00' in hud.btn_sleep.toolTip()

    # Hotkey Z triggers cycle
    ev_z = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.NoModifier)
    hud.keyPressEvent(ev_z)
    assert hud.sleep_timer_seconds == 30 * 60

    # Set Stop at end of passage (-1)
    hud._set_sleep_timer(-1)
    assert hud.sleep_timer_mode == -1
    assert 'end of current passage' in hud.btn_sleep.toolTip().lower()


def test_reading_progress_tracker(tmp_path, monkeypatch):
    from tts_reader import get_reading_state, save_reading_state

    state_file = str(tmp_path / 'reading_state.json')
    monkeypatch.setattr('tts_reader.READING_STATE_PATH', state_file)

    doc_file = str(tmp_path / 'test_book.pdf')
    with open(doc_file, 'w') as f:
        f.write('Sample PDF Content')

    assert get_reading_state(doc_file) is None

    save_reading_state(doc_file, chapter=14, total_chapters=120, title='My Audio Book')
    state = get_reading_state(doc_file)
    assert state is not None
    assert state['last_chapter'] == 14
    assert state['total_chapters'] == 120
    assert state['title'] == 'My Audio Book'


def test_obsidian_vault_bookmark_sync(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow

    vault_dir = tmp_path / 'MyVault'
    notes_dir = vault_dir / 'Reading Notes'
    os.makedirs(notes_dir, exist_ok=True)

    cfg_dir = str(tmp_path / 'cfg')
    os.makedirs(cfg_dir, exist_ok=True)
    monkeypatch.setattr('tts_text_processor.CONFIG_DIR', cfg_dir)
    monkeypatch.setattr('tts_hud.CONFIG_DIR', cfg_dir)

    with open(os.path.join(cfg_dir, 'settings.json'), 'w') as f:
        f.write(json.dumps({'obsidian_vault': str(vault_dir), 'obsidian_folder': 'Reading Notes'}))

    ctrl = MPVTimelineController()
    ctrl.current_time = 22.0
    ctrl.total_duration = 180.0
    ctrl.chunks_meta = [{'text': 'Insights for personal knowledge base.'}]
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    hud._on_bookmark_clicked()

    obs_file = notes_dir / 'TTS Bookmarks.md'
    assert obs_file.exists()
    content = obs_file.read_text(encoding='utf-8')
    assert '#tts/bookmark' in content
    assert 'Insights for personal knowledge base.' in content


def test_ssml_preservation_and_markup_detection():
    from tts_text_processor import has_ssml_tags, clean_and_filter_text

    assert has_ssml_tags('<emphasis level="strong">Important</emphasis>')
    assert has_ssml_tags('Take a pause <break time="400ms"/> here.')
    assert has_ssml_tags('<prosody pitch="+5Hz">High voice</prosody>')
    assert not has_ssml_tags('Plain text with no markup at all.')
    assert not has_ssml_tags('5 < 10 and 20 > 15')

    # Verify HTML stripping preserves SSML tags
    sample = '<div class="banner"><emphasis level="strong">Critical</emphasis> <break time="250ms"/> warning</div>'
    cleaned = clean_and_filter_text(sample)
    assert '<emphasis level="strong">Critical</emphasis>' in cleaned
    assert '<break time="250ms"/>' in cleaned
    assert '<div' not in cleaned
    assert '</div>' not in cleaned


def test_community_lexicon_importer(tmp_path):
    from tts_text_processor import import_community_lexicon, load_lexicon

    cfg_dir = str(tmp_path / 'cfg')
    os.makedirs(cfg_dir, exist_ok=True)

    added = import_community_lexicon(config_dir=cfg_dir)
    assert added > 30

    lex = load_lexicon(config_dir=cfg_dir)
    assert lex.get('kubectl') == 'kube control'
    assert lex.get('nginx', '').lower() == 'engine x'
    assert lex.get('pytorch') == 'pie torch'
    assert lex.get('fastapi') == 'fast A P I'


def test_audio_cache_encryption_roundtrip(tmp_path, monkeypatch):
    from tts_text_processor import (
        get_or_create_cache_encryption_key,
        encrypt_audio_data,
        decrypt_audio_data,
    )

    kfile = tmp_path / '.cache_key'
    monkeypatch.setattr('tts_text_processor.CACHE_KEY_PATH', str(kfile))

    raw_audio = b'ID3\x04\x00\x00\x00\x00\x00\x23FAKE_MP3_AUDIO_BYTES_TESTING_123'
    key = get_or_create_cache_encryption_key()
    assert len(key) == 44

    encrypted = encrypt_audio_data(raw_audio, key=key)
    assert encrypted != raw_audio
    assert encrypted.startswith(b'gAAAAA')

    decrypted = decrypt_audio_data(encrypted, key=key)
    assert decrypted == raw_audio


def test_reading_analytics_recording_and_summary(tmp_path, monkeypatch):
    from tts_text_processor import record_playback_activity, get_analytics_summary

    stats_file = tmp_path / 'stats.json'
    monkeypatch.setattr('tts_text_processor.STATS_JSON_PATH', str(stats_file))

    # Record 180 seconds at 1.5x speed with 600 words
    record_playback_activity(180.0, words_count=600, speed=1.5)

    summary = get_analytics_summary()
    assert summary['today_minutes'] == 3.0
    assert summary['today_words'] == 600
    assert summary['week_minutes'] >= 3.0
    assert summary['week_words'] >= 600
    assert summary['saved_minutes'] > 0.5


def test_audiobook_bundler_and_duration_measurement(tmp_path, monkeypatch):
    from tts_reader import get_audio_duration_seconds, bundle_documents_to_audiobook

    # Create dummy mp3 file
    mp3_file = tmp_path / 'dummy.mp3'
    mp3_file.write_bytes(b'A' * 32000)

    dur = get_audio_duration_seconds(str(mp3_file))
    assert dur > 0.0

    docs = [
        {'title': 'Chapter 1: The Beginning', 'text': 'Once upon a time in software engineering.'},
        {'title': 'Chapter 2: The Breakthrough', 'text': 'Async architectures transformed the industry.'},
    ]

    out_book = tmp_path / 'audiobook.mp3'

    # Generate a real valid 1-second silent MP3 sample for ffmpeg concatenation
    valid_sample = tmp_path / 'sample.mp3'
    import subprocess
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono', '-t', '1', '-q:a', '9', '-acodec', 'libmp3lame', str(valid_sample)],
        capture_output=True, check=True
    )
    valid_mp3_bytes = valid_sample.read_bytes()

    # Mock synthesize_segment_sync to produce valid mp3 audio
    def fake_synth(text, output_path, **kwargs):
        with open(output_path, 'wb') as f:
            f.write(valid_mp3_bytes)
        return {'ok': True, 'engine': 'test'}

    monkeypatch.setattr('tts_synth_worker.synthesize_segment_sync', fake_synth)

    ok = bundle_documents_to_audiobook(docs, output_path=str(out_book), book_title='Unit Test Book')
    assert ok is True
    assert out_book.exists()
    assert out_book.stat().st_size > 500


def test_parse_rate_multiplier():
    from tts_text_processor import parse_rate_multiplier

    assert parse_rate_multiplier('+50%') == 1.50
    assert parse_rate_multiplier('+30%') == 1.30
    assert parse_rate_multiplier('+10%') == 1.10
    assert parse_rate_multiplier('+0%') == 1.00
    assert parse_rate_multiplier('0%') == 1.00
    assert parse_rate_multiplier('-20%') == 0.80
    assert parse_rate_multiplier('-50%') == 0.50
    assert parse_rate_multiplier('') == 1.00
    assert parse_rate_multiplier('invalid') == 1.00


def test_effective_speed_and_language_switching(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow
    import json

    work_dir = tmp_path / 'work'
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr('tts_hud.WORK_DIR', str(work_dir))
    cfg_dir = tmp_path / 'cfg'
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr('tts_hud.CONFIG_DIR', str(cfg_dir))

    manifest = {
        'session_id': 'sess_speed_test',
        'title': 'Multilingual Speed Test',
        'chunks': [
            {'index': 0, 'lang': 'ar', 'rate': '+20%', 'rate_multiplier': 1.20, 'est_duration': 3.0, 'text': 'مرحبا بالعالم'},
            {'index': 1, 'lang': 'en', 'rate': '+50%', 'rate_multiplier': 1.50, 'est_duration': 3.0, 'text': 'Hello world'},
        ],
    }
    (work_dir / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (work_dir / 'seg_000.rate').write_text('+20%\n', encoding='utf-8')
    (work_dir / 'seg_001.rate').write_text('+50%\n', encoding='utf-8')

    ctrl = MPVTimelineController()
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # 1. Active segment 0 (Arabic +20% with base player speed 1.0x -> 1.2x)
    ctrl.active_segment_index = 0
    ctrl.speed = 1.0
    state0 = ctrl.refresh('PLAYING')
    assert state0['active_lang'] == 'ar'
    assert state0['rate_multiplier'] == 1.20
    assert state0['effective_speed'] == 1.20

    hud.update_from_state('PLAYING')
    assert hud.btn_speed.text() == '1.2x'
    assert '1.2x' in hud.btn_speed.toolTip()
    assert 'AR' in hud.btn_speed.toolTip()

    # 2. Advance to segment 1 (English +50% with base player speed 1.0x -> 1.5x)
    ctrl.active_segment_index = 1
    state1 = ctrl.refresh('PLAYING')
    assert state1['active_lang'] == 'en'
    assert state1['rate_multiplier'] == 1.50
    assert state1['effective_speed'] == 1.50

    hud.update_from_state('PLAYING')
    assert hud.btn_speed.text() == '1.5x'
    assert '1.5x' in hud.btn_speed.toolTip()
    assert 'EN' in hud.btn_speed.toolTip()

    # 3. Change base player speed to 1.2x -> English effective is round(1.50 * 1.2, 2) = 1.8x
    ctrl.set_speed(1.2)
    state1_boost = ctrl.refresh('PLAYING')
    assert state1_boost['effective_speed'] == 1.80

    hud.update_from_state('PLAYING')
    assert hud.btn_speed.text() == '1.8x'


def test_hud_theme_idempotence_and_word_karaoke(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow
    import json

    ctrl = MPVTimelineController()
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Rapid theme switches must be idempotent and crash-free
    for _ in range(3):
        hud.apply_theme('light')
        qwin.apply_theme('light')
        assert hud._current_theme == 'light'
        assert qwin._current_theme == 'light'

        hud.apply_theme('dark')
        qwin.apply_theme('dark')
        assert hud._current_theme == 'dark'
        assert qwin._current_theme == 'dark'

    # Test word karaoke color contrast between dark and light themes
    work_dir = tmp_path / 'work_karaoke'
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr('tts_hud.WORK_DIR', str(work_dir))

    words_meta = [
        {'word': 'High-performance', 'start': 0.0, 'end': 0.8},
        {'word': 'speech', 'start': 0.8, 'end': 1.4},
        {'word': 'synthesis', 'start': 1.4, 'end': 2.2},
    ]
    (work_dir / 'seg_000.words.json').write_text(json.dumps(words_meta), encoding='utf-8')

    ctrl.session_id = 'karaoke_test'
    ctrl.chunk_durations = [3.0]
    ctrl.current_time = 1.0  # Spoken word is 'speech' (index 1)
    chunks_meta = [{'text': 'High-performance speech synthesis', 'lang': 'en'}]

    # In Dark theme:
    hud.apply_theme('dark')
    hud.transcript_box.setVisible(True)
    hud._sync_transcript('karaoke_test', chunks_meta, active_idx=0)
    lbl = hud._transcript_rows[0].findChild(type(hud.status_dot))
    assert lbl is not None
    dark_html = lbl.text()
    assert '#4F46E5' in dark_html  # active word highlight background
    assert '#9CA3AF' in dark_html  # past word in dark mode

    # In Light theme:
    hud.apply_theme('light')
    hud._sync_transcript('karaoke_test', chunks_meta, active_idx=0)
    lbl_light = hud._transcript_rows[0].findChild(type(hud.status_dot))
    assert lbl_light is not None
    light_html = lbl_light.text()
    assert '#4F46E5' in light_html
    assert '#64748B' in light_html  # past word in light mode (high contrast)


def test_multi_screen_window_clamping(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    from tts_hud import FloatingPlayerWindow, MPVTimelineController, QueueManagerWindow

    ctrl = MPVTimelineController()
    qwin = QueueManagerWindow()
    hud = FloatingPlayerWindow(ctrl, qwin)

    # Mock an off-screen saved position
    monkeypatch.setattr('tts_hud.load_ui_positions', lambda: {
        'floating_player': {'x': 99999, 'y': -5000},
        'queue_window': {'x': -2000, 'y': 99999},
    })

    hud._restore_position()
    qwin._restore_position()

    screen = QApplication.primaryScreen()
    if screen is not None:
        geom = screen.availableGeometry()
        assert hud.x() < int(geom.right())
        assert hud.y() >= int(geom.top())
        assert qwin.x() >= int(geom.left())
        assert qwin.y() < int(geom.bottom())


def test_daemon_tick_resilience(tmp_path, monkeypatch):
    import importlib.util
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    spec = importlib.util.spec_from_file_location('tts_daemon', os.path.join(SCRIPT_DIR, 'tts-daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    st_file = tmp_path / 'status'
    st_file.write_text('IDLE\n', encoding='utf-8')
    monkeypatch.setattr(mod, 'STATUS_FILE', str(st_file))

    # Initialize a mock daemon instance
    daemon = mod.QtTTSDaemon.__new__(mod.QtTTSDaemon)
    daemon._state = 'IDLE'
    daemon._playing_accum_sec = 0.0
    daemon.log = mod.logging.getLogger('test-daemon')
    daemon.tray = type('MockTray', (), {'setToolTip': lambda *a: None, 'setIcon': lambda *a: None})()
    daemon._icons = {'IDLE': None}
    daemon.controller = type('MockCtrl', (), {'speed': 1.0})()
    daemon.ducker = type('MockDucker', (), {'duck': lambda *a: None, 'unduck': lambda *a: None})()
    daemon.player_window = type('MockPlayerWin', (), {'update_from_state': lambda *a: None})()
    daemon._sync_media_keys = lambda *a: None
    daemon._check_bluetooth_audio = lambda: None

    # Ticking repeatedly when state == _state must NEVER raise UnboundLocalError
    daemon._tick_internal()
    daemon._tick_internal()

    # Transitioning from PLAYING to IDLE must also run cleanly
    daemon._state = 'PLAYING'
    st_file.write_text('IDLE\n', encoding='utf-8')
    daemon._tick_internal()
    assert daemon._state == 'IDLE'







