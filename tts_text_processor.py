#!/usr/bin/env python3
"""
Smart Text Processor & Trilingual Segmenter for Speak a Loud Universal.

Combines:
1. Input sanitization (control chars, bidi marks, length cap)
2. Markdown / URL / code / comparison-symbol cleaning (inspired by obsidian-edge-tts)
3. Trilingual language detection & segmentation (Arabic, German, English)
4. Fast-first-chunk sentence & bullet-list splitting (~220 chars for seg_000, ~480 for rest)
5. Manifest generation (/tmp/speak-aloud-work/manifest.json) for Floating Player HUD
"""

import argparse
import hashlib
import json
import os
import re
import sys
import shutil
import subprocess
import tempfile
import time
import unicodedata
from typing import Any, Dict, List, Optional

# ── Runtime Directory & Paths ($XDG_RUNTIME_DIR/speak-aloud-$UID) ─────────────

CONFIG_DIR = os.path.expanduser('~/.config/tts_settings')
SETTINGS_JSON_PATH = os.path.join(CONFIG_DIR, 'settings.json')
LEXICON_JSON_PATH = os.path.join(CONFIG_DIR, 'lexicon.json')
AUDIO_CACHE_DIR = os.path.expanduser('~/.cache/speak-aloud/audio')
READING_STATE_PATH = os.path.expanduser('~/.local/share/speak-aloud/reading_state.json')
STATS_JSON_PATH = os.path.expanduser('~/.local/share/speak-aloud/stats.json')
CACHE_KEY_PATH = os.path.join(CONFIG_DIR, '.cache_key')

SSML_TAGS = {
    'speak', 'emphasis', 'break', 'prosody', 'say-as',
    'phoneme', 'sub', 'voice', 'lang', 'p', 's', 'mstts:express-as'
}
SSML_TAG_PATTERN = re.compile(
    r'</?(?:speak|emphasis|break|prosody|say-as|phoneme|sub|voice|lang|mstts:express-as)(?:\s+[^>]*)?>',
    re.IGNORECASE
)


def has_ssml_tags(text: str) -> bool:
    """Return True if text contains recognized SSML markup tags."""
    if not text:
        return False
    return bool(SSML_TAG_PATTERN.search(text))


def get_runtime_dir() -> str:
    """Return per-user 0700 runtime directory (${XDG_RUNTIME_DIR:-/tmp}/speak-aloud-$UID)."""
    uid = os.getuid()
    base = os.environ.get('XDG_RUNTIME_DIR') or '/tmp'
    if not os.path.isdir(base) or not os.access(base, os.W_OK):
        base = '/tmp'
    rdir = os.path.join(base, f'speak-aloud-{uid}')
    os.makedirs(rdir, mode=0o700, exist_ok=True)
    try:
        os.chmod(rdir, 0o700)
    except OSError:
        pass
    return rdir


def get_work_dir() -> str:
    wdir = os.path.join(get_runtime_dir(), 'work')
    os.makedirs(wdir, mode=0o700, exist_ok=True)
    return wdir


def get_mpv_socket_path() -> str:
    return os.path.join(get_runtime_dir(), 'mpv.sock')


def get_synth_socket_path() -> str:
    return os.path.join(get_runtime_dir(), 'synth.sock')


def get_status_path() -> str:
    return os.path.join(get_runtime_dir(), 'status')


def get_pid_path() -> str:
    return os.path.join(get_runtime_dir(), 'pid')


def get_queue_path() -> str:
    return os.path.join(get_runtime_dir(), 'queue.json')


def get_queue_state_path() -> str:
    return os.path.join(get_runtime_dir(), 'queue_state.json')


def read_bool_config(filename: str, default: bool = True) -> bool:
    """Read a boolean value ('yes'/'no', 'true'/'false') from a config file in CONFIG_DIR."""
    try:
        with open(os.path.join(CONFIG_DIR, filename), 'r', encoding='utf-8') as f:
            val = f.read().strip().lower()
        return val in ('yes', 'true', '1', 'on')
    except Exception:
        return default


def read_str_config(filename: str, default: str = '') -> str:
    """Read a single-line string value from a config file in CONFIG_DIR."""
    try:
        with open(os.path.join(CONFIG_DIR, filename), 'r', encoding='utf-8') as f:
            val = f.readline().strip()
        return val if val else default
    except Exception:
        return default


_THEME_CACHE: Dict[str, Any] = {'time': 0.0, 'val': 'dark'}


def invalidate_theme_cache() -> None:
    """Invalidate cached system theme detection to force immediate re-query."""
    _THEME_CACHE['time'] = 0.0


def parse_rate_multiplier(rate_str: str) -> float:
    """Convert a rate string (e.g. '+50%', '+30%', '-20%', '0%') to a multiplier float (e.g. 1.50, 1.30, 0.80, 1.00)."""
    if not rate_str:
        return 1.0
    m = re.search(r'([+-]?\d+)', str(rate_str))
    if not m:
        return 1.0
    try:
        pct = float(m.group(1))
        return max(0.2, min(4.0, round(1.0 + (pct / 100.0), 3)))
    except Exception:
        return 1.0


def get_effective_theme(app: Any = None, max_age_sec: float = 3.0) -> str:
    """Determine effective UI theme ('dark' or 'light') matching system default or user override."""
    pref = read_str_config('ui_theme', 'auto').strip().lower()
    if pref in ('dark', 'light'):
        return pref

    now = time.time()
    if max_age_sec > 0 and (now - _THEME_CACHE['time']) < max_age_sec:
        return _THEME_CACHE['val']

    resolved = _THEME_CACHE.get('val', 'dark')
    theme_detected = False

    # 1. Primary on Cinnamon/Linux Mint: Check Cinnamon desktop themes
    for schema in ('org.cinnamon.desktop.interface', 'org.gnome.desktop.interface', 'org.cinnamon.theme'):
        try:
            key = 'name' if schema == 'org.cinnamon.theme' else 'gtk-theme'
            res = subprocess.run(
                ['gsettings', 'get', schema, key],
                capture_output=True, text=True, timeout=0.6
            )
            if res.returncode == 0:
                t_name = res.stdout.strip().strip("'\"").lower()
                if t_name:
                    if 'dark' in t_name or 'black' in t_name:
                        resolved = 'dark'
                        theme_detected = True
                        break
                    elif 'light' in t_name or 'mint-y' in t_name or 'adwaita' in t_name or 'arc' in t_name or 'yaru' in t_name:
                        resolved = 'light'
                        theme_detected = True
                        break
        except Exception:
            pass

    # 2. Check FreeDesktop / GNOME color-scheme portal
    if not theme_detected:
        try:
            res = subprocess.run(
                ['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'],
                capture_output=True, text=True, timeout=0.6
            )
            if res.returncode == 0:
                out = res.stdout.strip().lower()
                if 'prefer-dark' in out:
                    resolved = 'dark'
                    theme_detected = True
                elif 'prefer-light' in out:
                    resolved = 'light'
                    theme_detected = True
        except Exception:
            pass

    # 3. Check Qt application palette background lightness
    if not theme_detected and app is not None:
        try:
            lightness = app.palette().window().color().lightness()
            resolved = 'dark' if lightness < 128 else 'light'
            theme_detected = True
        except Exception:
            pass

    _THEME_CACHE['time'] = now
    _THEME_CACHE['val'] = resolved
    return resolved


def get_sanitized_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return a hardened, sanitized environment dictionary for subprocess execution."""
    safe_keys = {
        'DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS',
        'XDG_RUNTIME_DIR', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE',
        'PULSE_SERVER', 'WAYLAND_DISPLAY', 'HOME', 'USER',
        'LANG', 'LC_ALL', 'TERM',
    }
    clean = {k: os.environ[k] for k in safe_keys if k in os.environ}
    home_bin = os.path.expanduser('~/.local/bin')
    clean['PATH'] = f"/usr/local/bin:/usr/bin:/bin:/snap/bin:{home_bin}"
    if extra:
        clean.update(extra)
    return clean


def format_pitch_str(raw: Any) -> str:
    """Format and validate pitch string (e.g. '+0Hz', '-5Hz', '+10Hz')."""
    if raw is None:
        return '+0Hz'
    s = str(raw).strip()
    m = re.match(r'^([+-]?[0-9]+)\s*(Hz|%)?$', s, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        val = max(-50, min(50, val))
        unit = m.group(2) or 'Hz'
        return f"{val:+d}{unit}"
    return '+0Hz'


def get_audio_cache_path(text: str, voice: str, rate: str, pitch: str = '+0Hz') -> str:
    """Generate SHA256 content-addressed cache path for an audio segment."""
    os.makedirs(AUDIO_CACHE_DIR, mode=0o700, exist_ok=True)
    norm_txt = text.strip()
    norm_voice = voice.strip()
    norm_rate = _format_rate_str(rate)
    norm_pitch = format_pitch_str(pitch)
    key = hashlib.sha256(f"{norm_txt}:{norm_voice}:{norm_rate}:{norm_pitch}".encode('utf-8')).hexdigest()
    return os.path.join(AUDIO_CACHE_DIR, f"{key}.mp3")


def prune_audio_cache(max_size_mb: int = 500, max_age_days: int = 14) -> Dict[str, int]:
    """Prune audio cache using LRU order when size > max_size_mb or age > max_age_days."""
    if not os.path.isdir(AUDIO_CACHE_DIR):
        return {'deleted_files': 0, 'reclaimed_bytes': 0}

    now = time.time()
    max_age_sec = max_age_days * 86400
    max_bytes = max_size_mb * 1024 * 1024

    files_info: List[tuple] = []
    total_bytes = 0
    deleted_files = 0
    reclaimed_bytes = 0

    try:
        for entry in os.scandir(AUDIO_CACHE_DIR):
            if entry.is_file():
                try:
                    stat = entry.stat()
                    total_bytes += stat.st_size
                    # Check file age first
                    if (now - stat.st_mtime) > max_age_sec:
                        os.unlink(entry.path)
                        words_file = os.path.splitext(entry.path)[0] + '.words.json'
                        if os.path.exists(words_file):
                            try:
                                os.unlink(words_file)
                            except OSError:
                                pass
                        deleted_files += 1
                        reclaimed_bytes += stat.st_size
                        continue
                    files_info.append((stat.st_atime or stat.st_mtime, stat.st_size, entry.path))
                except OSError:
                    continue
    except OSError:
        pass

    # If cache still exceeds max_bytes, delete oldest accessed
    cur_bytes = total_bytes - reclaimed_bytes
    if cur_bytes > max_bytes:
        files_info.sort(key=lambda x: x[0])  # Oldest accessed first
        for atime, fsize, fpath in files_info:
            try:
                os.unlink(fpath)
                words_file = os.path.splitext(fpath)[0] + '.words.json'
                if os.path.exists(words_file):
                    try:
                        os.unlink(words_file)
                    except OSError:
                        pass
                deleted_files += 1
                reclaimed_bytes += fsize
                cur_bytes -= fsize
                if cur_bytes <= max_bytes * 0.85:  # Headroom
                    break
            except OSError:
                continue

    return {'deleted_files': deleted_files, 'reclaimed_bytes': reclaimed_bytes}


def prune_runtime_dir(max_age_hours: int = 24) -> int:
    """Prune old session workdirs and temporary artifacts in runtime directory."""
    rdir = get_runtime_dir()
    if not os.path.isdir(rdir):
        return 0
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    pruned = 0
    try:
        for entry in os.scandir(rdir):
            if entry.is_dir() and entry.name.startswith(('work_', 'session_')):
                try:
                    stat = entry.stat()
                    if stat.st_mtime < cutoff:
                        shutil.rmtree(entry.path, ignore_errors=True)
                        pruned += 1
                except OSError:
                    pass
            elif entry.is_file() and entry.name.endswith(('.tmp', '.mp3', '.wav', '.log')):
                try:
                    stat = entry.stat()
                    if stat.st_mtime < cutoff:
                        os.unlink(entry.path)
                        pruned += 1
                except OSError:
                    pass
    except OSError:
        pass
    return pruned


def load_queue() -> List[Dict[str, Any]]:
    """Load playback queue items from $RUNTIME_DIR/queue.json."""
    path = get_queue_path()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_queue(items: List[Dict[str, Any]]) -> None:
    """Atomically persist playback queue items to $RUNTIME_DIR/queue.json."""
    path = get_queue_path()
    atomic_write_json(path, items, mode=0o600)


def get_keyring_api_key() -> Optional[str]:
    """Retrieve translation API key from desktop keyring (secret-tool) if available."""
    if not shutil.which('secret-tool'):
        return None
    try:
        res = subprocess.run(
            ['secret-tool', 'lookup', 'application', 'speak-aloud'],
            capture_output=True, text=True, timeout=1.0
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None


def store_keyring_api_key(key: str) -> bool:
    """Store translation API key securely in system keyring via secret-tool."""
    if not shutil.which('secret-tool'):
        return False
    try:
        if key:
            res = subprocess.run(
                ['secret-tool', 'store', '--label=Speak a Loud Translation API Key', 'application', 'speak-aloud'],
                input=key, text=True, capture_output=True, timeout=2.0
            )
            return res.returncode == 0
        else:
            res = subprocess.run(
                ['secret-tool', 'clear', 'application', 'speak-aloud'],
                capture_output=True, timeout=1.0
            )
            return res.returncode == 0
    except Exception:
        return False


def ensure_runtime_symlinks() -> None:
    """Maintain user-owned symlinks in /tmp for backward compatibility."""
    uid = os.getuid()
    links = {
        '/tmp/speak-aloud-work': get_work_dir(),
        '/tmp/speak-aloud-mpv.sock': get_mpv_socket_path(),
        '/tmp/speak-aloud-synth.sock': get_synth_socket_path(),
        '/tmp/speak-aloud-status': get_status_path(),
        '/tmp/speak-aloud.pid': get_pid_path(),
        '/tmp/speak-aloud-queue.json': get_queue_path(),
        '/tmp/speak-aloud-queue-state.json': get_queue_state_path(),
    }
    for legacy_path, target_path in links.items():
        if os.path.abspath(legacy_path) == os.path.abspath(target_path):
            continue
        try:
            if os.path.islink(legacy_path):
                st = os.lstat(legacy_path)
                if st.st_uid == uid:
                    if os.readlink(legacy_path) != target_path:
                        os.unlink(legacy_path)
                        os.symlink(target_path, legacy_path)
                continue
            if os.path.exists(legacy_path):
                st = os.lstat(legacy_path)
                if st.st_uid != uid:
                    continue
                if os.path.isdir(legacy_path):
                    import shutil
                    shutil.rmtree(legacy_path, ignore_errors=True)
                else:
                    os.unlink(legacy_path)
            os.symlink(target_path, legacy_path)
        except OSError:
            pass


# ── Atomic File & JSON Helpers ────────────────────────────────────────────────

def atomic_write_text(path: str, content: str, mode: int = 0o600) -> None:
    """Atomically write text to `path` via tempfile + fsync + os.replace."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp_', dir=parent, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data: Any, mode: int = 0o600) -> None:
    """Atomically serialize `data` as JSON to `path`."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    atomic_write_text(path, text, mode=mode)


# ── Pronunciation Lexicon / Custom Alias Dictionary ───────────────────────────

DEFAULT_LEXICON: Dict[str, str] = {
    'kubectl': 'kube control',
    'PyQt6': 'Pie Cute 6',
    'PyQt5': 'Pie Cute 5',
    'nginx': 'engine X',
    'SQLite': 'S Q Lite',
    'PostgreSQL': 'Postgres Q L',
    'MySQL': 'My S Q L',
    'systemd': 'system D',
    'journalctl': 'journal control',
    'systemctl': 'system control',
    'FastAPI': 'Fast A P I',
    'WebSocket': 'Web Socket',
    'stdio': 'standard I O',
    'stdin': 'standard in',
    'stdout': 'standard out',
    'stderr': 'standard error',
    'regex': 'reg-ex',
    'mpv': 'M P V',
    'xclip': 'X clip',
    'xdotool': 'X do tool',
    'PipeWire': 'Pipe Wire',
    'PulseAudio': 'Pulse Audio',
}


def load_lexicon(config_dir: Optional[str] = None) -> Dict[str, str]:
    """Load pronunciation lexicon from lexicon.json, initializing defaults if missing."""
    cdir = config_dir or CONFIG_DIR
    lpath = os.path.join(cdir, 'lexicon.json')
    if not os.path.exists(lpath):
        try:
            atomic_write_json(lpath, DEFAULT_LEXICON)
        except OSError:
            pass
        return dict(DEFAULT_LEXICON)
    try:
        with open(lpath, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return dict(DEFAULT_LEXICON)
        cleaned: Dict[str, str] = {}
        for k, v in raw.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs and len(ks) <= 80 and len(vs) <= 160:
                cleaned[ks] = vs
        return cleaned
    except Exception:
        return dict(DEFAULT_LEXICON)


def save_lexicon(mapping: Dict[str, str], config_dir: Optional[str] = None) -> Dict[str, str]:
    """Validate and atomically save pronunciation lexicon to lexicon.json."""
    cdir = config_dir or CONFIG_DIR
    lpath = os.path.join(cdir, 'lexicon.json')
    cleaned: Dict[str, str] = {}
    for k, v in (mapping or {}).items():
        ks = CTRL_CHARS_RE.sub('', str(k)).strip()
        vs = CTRL_CHARS_RE.sub('', str(v)).strip()
        if ks and vs and len(ks) <= 80 and len(vs) <= 160:
            cleaned[ks] = vs
    atomic_write_json(lpath, cleaned)
    return cleaned


def apply_lexicon(text: str, lexicon: Optional[Dict[str, str]] = None, config_dir: Optional[str] = None) -> str:
    """Apply case-insensitive word-boundary pronunciation replacements."""
    if not text:
        return text
    lex = lexicon if lexicon is not None else load_lexicon(config_dir=config_dir)
    if not lex:
        return text
    # Sort longest terms first so specific terms match before substrings
    for term in sorted(lex.keys(), key=len, reverse=True):
        spoken = lex[term]
        pattern = re.compile(r'(?<!\w)' + re.escape(term) + r'(?!\w)', re.IGNORECASE)
        text = pattern.sub(spoken, text)
    return text


COMMUNITY_LEXICON = {
    'kubectl': 'kube control',
    'k8s': 'kates',
    'nginx': 'engine x',
    'postgresql': 'post gress Q L',
    'postgres': 'post gress',
    'scikit-learn': 'sigh kit learn',
    'sklearn': 'S K learn',
    'pytorch': 'pie torch',
    'cuda': 'koo duh',
    'vllm': 'V L L M',
    'gui': 'gooey',
    'cli': 'C L I',
    'api': 'A P I',
    'sdk': 'S D K',
    'jwt': 'jot',
    'json': 'jay son',
    'yaml': 'yamel',
    'ci/cd': 'C I C D',
    'pr': 'P R',
    'repo': 'ree po',
    'stdout': 'standard out',
    'stderr': 'standard error',
    'stdin': 'standard in',
    'sysadmin': 'sys admin',
    'regex': 'reg ex',
    'uuid': 'U U I D',
    'guid': 'goo id',
    'sudo': 'sue due',
    'chown': 'C H own',
    'chmod': 'C H mod',
    'tmux': 'tea mucks',
    'vim': 'vim',
    'neovim': 'neo vim',
    'pyqt6': 'pie cute 6',
    'pyside6': 'pie side 6',
    'fastapi': 'fast A P I',
    'django': 'jang go',
    'celery': 'celery',
    'redis': 'red iss',
    'sqlite': 'sequel lite',
    'mysql': 'my sequel',
    'nosql': 'no sequel',
    'graphql': 'graph Q L',
    'oauth': 'O auth',
    'wasm': 'wah zum',
    'npm': 'N P M',
    'pnpm': 'P N P M',
    'pipx': 'pip X',
    'venv': 'V env',
    'dockerfile': 'docker file',
    'kubernetes': 'koo ber net ees',
}


def import_community_lexicon(url_or_path: Optional[str] = None, config_dir: Optional[str] = None) -> int:
    """Merge community developer pronunciations into lexicon.json."""
    current = load_lexicon(config_dir=config_dir)
    imported_dict: Dict[str, str] = dict(COMMUNITY_LEXICON)

    if url_or_path:
        url_s = url_or_path.strip()
        if url_s.startswith(('http://', 'https://')):
            import urllib.request
            try:
                req = urllib.request.Request(url_s, headers={'User-Agent': 'SpeakALoud-Lexicon-Importer'})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    raw_text = resp.read().decode('utf-8', errors='replace')
                # Try parsing as JSON first
                try:
                    parsed = json.loads(raw_text)
                    if isinstance(parsed, dict):
                        imported_dict.update({str(k): str(v) for k, v in parsed.items()})
                except Exception:
                    # Parse as line-separated "key = value"
                    for line in raw_text.splitlines():
                        if '=' in line:
                            k, v = line.split('=', 1)
                            if k.strip() and v.strip():
                                imported_dict[k.strip()] = v.strip()
            except Exception:
                pass
        elif os.path.isfile(url_s):
            try:
                with open(url_s, 'r', encoding='utf-8') as f:
                    raw_text = f.read()
                try:
                    parsed = json.loads(raw_text)
                    if isinstance(parsed, dict):
                        imported_dict.update({str(k): str(v) for k, v in parsed.items()})
                except Exception:
                    for line in raw_text.splitlines():
                        if '=' in line:
                            k, v = line.split('=', 1)
                            if k.strip() and v.strip():
                                imported_dict[k.strip()] = v.strip()
            except Exception:
                pass

    count_new = 0
    for k, v in imported_dict.items():
        if k not in current:
            current[k] = v
            count_new += 1

    save_lexicon(current, config_dir=config_dir)
    return count_new


def get_or_create_cache_encryption_key() -> bytes:
    """Retrieve or generate 32-byte Fernet key for encrypting audio cache at rest."""
    import base64
    # 1. Try keyring
    keyring_key = None
    if shutil.which('secret-tool'):
        try:
            res = subprocess.run(
                ['secret-tool', 'lookup', 'application', 'speak-aloud', 'key', 'cache_encryption'],
                capture_output=True, text=True, timeout=1.0
            )
            val = res.stdout.strip()
            if val and len(val) >= 32:
                keyring_key = val.encode('utf-8')
        except Exception:
            pass

    if keyring_key:
        return keyring_key

    # 2. Check hidden local key file
    if os.path.isfile(CACHE_KEY_PATH):
        try:
            with open(CACHE_KEY_PATH, 'rb') as f:
                k = f.read().strip()
            if len(k) >= 32:
                return k
        except OSError:
            pass

    # 3. Generate fresh Fernet key
    from cryptography.fernet import Fernet
    new_key = Fernet.generate_key()
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    try:
        with open(CACHE_KEY_PATH, 'wb') as f:
            f.write(new_key)
        os.chmod(CACHE_KEY_PATH, 0o600)
    except OSError:
        pass

    # Save to keyring as well if available
    if shutil.which('secret-tool'):
        try:
            subprocess.run(
                ['secret-tool', 'store', '--label=Speak a Loud Cache Key', 'application', 'speak-aloud', 'key', 'cache_encryption'],
                input=new_key.decode('utf-8') + '\n', text=True, timeout=1.5, stdout=subprocess.DEVNULL
            )
        except Exception:
            pass

    return new_key


def encrypt_audio_data(data: bytes, key: Optional[bytes] = None) -> bytes:
    """Encrypt raw audio bytes using Fernet authenticated symmetric encryption."""
    from cryptography.fernet import Fernet
    k = key or get_or_create_cache_encryption_key()
    f = Fernet(k)
    return f.encrypt(data)


def decrypt_audio_data(data: bytes, key: Optional[bytes] = None) -> bytes:
    """Decrypt Fernet-encrypted raw audio bytes."""
    from cryptography.fernet import Fernet
    k = key or get_or_create_cache_encryption_key()
    f = Fernet(k)
    return f.decrypt(data)


def record_playback_activity(delta_seconds: float, words_count: int = 0, speed: float = 1.0) -> None:
    """Record listening activity into ~/.local/share/speak-aloud/stats.json."""
    if delta_seconds <= 0:
        return
    os.makedirs(os.path.dirname(STATS_JSON_PATH), mode=0o700, exist_ok=True)
    today = time.strftime('%Y-%m-%d')
    stats: Dict[str, Any] = {}
    if os.path.isfile(STATS_JSON_PATH):
        try:
            with open(STATS_JSON_PATH, 'r', encoding='utf-8') as f:
                stats = json.load(f)
        except Exception:
            stats = {}

    day_entry = stats.get(today, {'seconds': 0.0, 'words': 0, 'sessions': 0, 'speed': speed})
    day_entry['seconds'] = round(day_entry.get('seconds', 0.0) + delta_seconds, 1)
    day_entry['words'] = day_entry.get('words', 0) + max(0, words_count)
    day_entry['speed'] = round(speed, 2)
    stats[today] = day_entry

    tmp_p = f"{STATS_JSON_PATH}.tmp.{os.getpid()}"
    try:
        with open(tmp_p, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2)
        os.replace(tmp_p, STATS_JSON_PATH)
    finally:
        if os.path.exists(tmp_p):
            try:
                os.unlink(tmp_p)
            except OSError:
                pass


def get_analytics_summary() -> Dict[str, Any]:
    """Calculate daily and weekly listening metrics."""
    today_str = time.strftime('%Y-%m-%d')
    stats: Dict[str, Any] = {}
    if os.path.isfile(STATS_JSON_PATH):
        try:
            with open(STATS_JSON_PATH, 'r', encoding='utf-8') as f:
                stats = json.load(f)
        except Exception:
            pass

    today_sec = stats.get(today_str, {}).get('seconds', 0.0)
    today_words = stats.get(today_str, {}).get('words', 0)

    # Calculate past 7 days
    now = time.time()
    week_sec = 0.0
    total_words = 0
    for day_offset in range(7):
        ds = time.strftime('%Y-%m-%d', time.localtime(now - (day_offset * 86400)))
        entry = stats.get(ds, {})
        week_sec += entry.get('seconds', 0.0)
        total_words += entry.get('words', 0)

    # Average human visual reading speed is ~230 wpm; TTS at 1.5x saves ~35% time
    saved_minutes = round((week_sec / 60.0) * 0.35, 1)

    return {
        'today_minutes': round(today_sec / 60.0, 1),
        'today_words': today_words,
        'week_minutes': round(week_sec / 60.0, 1),
        'week_words': total_words,
        'saved_minutes': saved_minutes,
    }


# ── Strict Schema Validation & Atomic Settings Store ──────────────────────────

VALID_PROVIDERS = {'google', 'mymemory', 'libretranslate', 'deepl', 'openai'}
VALID_LANGS = {'auto', 'en', 'ar', 'de', 'es', 'fr', 'it', 'pt', 'ru', 'zh', 'ja', 'ko', 'tr', 'nl', 'pl'}


def _parse_rate_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    m = re.search(r'[+-]?\d+', str(val))
    num = int(m.group(0)) if m else default
    return max(-50, min(100, num))


def _format_rate_str(val: Any) -> str:
    num = _parse_rate_int(val, 0)
    return f'+{num}%' if num >= 0 else f'{num}%'


def _parse_float_clamped(val: Any, low: float, high: float, default: float) -> float:
    try:
        v = float(str(val).strip().rstrip('x'))
    except (ValueError, TypeError):
        v = default
    return round(max(low, min(high, v)), 2)


def _parse_int_clamped(val: Any, low: int, high: int, default: int) -> int:
    try:
        v = int(float(str(val).strip()))
    except (ValueError, TypeError):
        v = default
    return max(low, min(high, v))


def _norm_yes_no(val: Any, default: bool = False) -> str:
    return 'yes' if to_bool(str(val) if val is not None else '', default) else 'no'


def _sanitize_str(val: Any, max_len: int = 256, default: str = '') -> str:
    if val is None:
        return default
    s = CTRL_CHARS_RE.sub('', str(val)).strip()
    return s[:max_len] if s else default


class TTSSettingsStore:
    """Schema-validated, atomic settings manager for ~/.config/tts_settings."""

    DEFAULTS: Dict[str, Any] = {
        'voice_en': 'en-US-ChristopherNeural',
        'voice_ar': 'ar-SA-HamedNeural',
        'voice_de': 'de-DE-ConradNeural',
        'custom_voice': '',
        'rate_en': '+0%',
        'rate_ar': '+0%',
        'rate_de': '+0%',
        'global_speed': '1.0',
        'show_hud': 'yes',
        'replay_at_end': 'yes',
        'filter_markdown': 'yes',
        'filter_urls': 'yes',
        'skip_code': 'no',
        'replace_symbols': 'yes',
        'use_lexicon': 'yes',
        'pause_punctuation': 'no',
        'pause_delay': '150',
        'translate_enabled': 'no',
        'translate_to': 'en',
        'translate_from': 'auto',
        'translate_provider': 'google',
        'translate_api_key': '',
        'piper_model_en': '',
        'piper_model_de': '',
        'piper_model_ar': '',
        'duck_background_audio': 'no',
        'duck_volume_percent': '40',
        'ui_theme': 'auto',
        'pitch': '+0Hz',
        'obsidian_vault': '',
        'obsidian_folder': 'Reading Notes',
        'sleep_timer_minutes': '0',
        'encrypt_audio_cache': 'no',
        'crossfade_audio': 'yes',
        'crossfade_duration': '40',
        'auto_pause_bluetooth': 'yes',
    }

    def __init__(self, config_dir: Optional[str] = None):
        self.config_dir = config_dir or sys.modules.get('tts_text_processor', sys.modules[__name__]).CONFIG_DIR
        self.json_path = os.path.join(self.config_dir, 'settings.json')

    @classmethod
    def validate(cls, raw: Dict[str, Any]) -> Dict[str, str]:
        """Validate and normalize a raw settings dictionary against strict schema rules."""
        src = dict(cls.DEFAULTS)
        if isinstance(raw, dict):
            src.update({k: v for k, v in raw.items() if v is not None})

        provider = _sanitize_str(src.get('translate_provider'), 32, 'google').lower()
        if provider not in VALID_PROVIDERS:
            provider = 'google'

        trans_to = _sanitize_str(src.get('translate_to'), 10, 'en').lower()
        if trans_to not in VALID_LANGS or trans_to == 'auto':
            trans_to = 'en'

        trans_from = _sanitize_str(src.get('translate_from'), 10, 'auto').lower()
        if trans_from not in VALID_LANGS:
            trans_from = 'auto'

        ui_theme = _sanitize_str(src.get('ui_theme'), 16, 'auto').lower()
        if ui_theme not in ('auto', 'dark', 'light'):
            ui_theme = 'auto'

        validated: Dict[str, str] = {
            'voice_en': _sanitize_str(src.get('voice_en'), 80, cls.DEFAULTS['voice_en']),
            'voice_ar': _sanitize_str(src.get('voice_ar'), 80, cls.DEFAULTS['voice_ar']),
            'voice_de': _sanitize_str(src.get('voice_de'), 80, cls.DEFAULTS['voice_de']),
            'custom_voice': _sanitize_str(src.get('custom_voice'), 80, ''),
            'rate_en': _format_rate_str(src.get('rate_en')),
            'rate_ar': _format_rate_str(src.get('rate_ar')),
            'rate_de': _format_rate_str(src.get('rate_de')),
            'global_speed': str(_parse_float_clamped(src.get('global_speed'), 0.5, 3.0, 1.0)),
            'show_hud': _norm_yes_no(src.get('show_hud'), True),
            'replay_at_end': _norm_yes_no(src.get('replay_at_end'), True),
            'filter_markdown': _norm_yes_no(src.get('filter_markdown'), True),
            'filter_urls': _norm_yes_no(src.get('filter_urls'), True),
            'skip_code': _norm_yes_no(src.get('skip_code'), False),
            'replace_symbols': _norm_yes_no(src.get('replace_symbols'), True),
            'use_lexicon': _norm_yes_no(src.get('use_lexicon'), True),
            'pause_punctuation': _norm_yes_no(src.get('pause_punctuation'), False),
            'pause_delay': str(_parse_int_clamped(src.get('pause_delay'), 0, 2000, 150)),
            'translate_enabled': _norm_yes_no(src.get('translate_enabled'), False),
            'translate_to': trans_to,
            'translate_from': trans_from,
            'translate_provider': provider,
            'translate_api_key': _sanitize_str(src.get('translate_api_key'), 256, ''),
            'piper_model_en': _sanitize_str(src.get('piper_model_en'), 512, ''),
            'piper_model_de': _sanitize_str(src.get('piper_model_de'), 512, ''),
            'piper_model_ar': _sanitize_str(src.get('piper_model_ar'), 512, ''),
            'duck_background_audio': _norm_yes_no(src.get('duck_background_audio'), False),
            'duck_volume_percent': str(_parse_int_clamped(src.get('duck_volume_percent'), 5, 90, 40)),
            'ui_theme': ui_theme,
            'pitch': format_pitch_str(src.get('pitch')),
            'obsidian_vault': _sanitize_str(src.get('obsidian_vault'), 128, ''),
            'obsidian_folder': _sanitize_str(src.get('obsidian_folder'), 128, 'Reading Notes'),
            'sleep_timer_minutes': str(_parse_int_clamped(src.get('sleep_timer_minutes'), 0, 240, 0)),
            'encrypt_audio_cache': _norm_yes_no(src.get('encrypt_audio_cache'), False),
            'crossfade_audio': _norm_yes_no(src.get('crossfade_audio'), True),
            'crossfade_duration': str(_parse_int_clamped(src.get('crossfade_duration'), 10, 200, 40)),
            'auto_pause_bluetooth': _norm_yes_no(src.get('auto_pause_bluetooth'), True),
        }
        return validated

    LEGACY_KEY_MAP: Dict[str, str] = {
        'voice': 'voice_en',
        'arabic_voice': 'voice_ar',
        'german_voice': 'voice_de',
        'rate': 'rate_en',
        'arabic_rate': 'rate_ar',
        'german_rate': 'rate_de',
        'source_language': 'translate_from',
        'show_floating_player': 'show_hud',
        'enable_replay': 'replay_at_end',
        'filter_code': 'skip_code',
        'pause_delay_ms': 'pause_delay',
        'translate_target': 'translate_to',
    }

    def load(self) -> Dict[str, str]:
        """Load settings from settings.json and overlay any flat config files."""
        os.makedirs(self.config_dir, mode=0o700, exist_ok=True)
        merged: Dict[str, Any] = dict(self.DEFAULTS)

        # First read legacy flat files as base fallback
        for legacy_key, schema_key in self.LEGACY_KEY_MAP.items():
            lpath = os.path.join(self.config_dir, legacy_key)
            if os.path.exists(lpath):
                try:
                    with open(lpath, 'r', encoding='utf-8') as f:
                        val = f.readline().strip()
                    if val:
                        merged[schema_key] = val
                except OSError:
                    pass

        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    merged.update(data)
            except Exception:
                pass

        for key in self.DEFAULTS:
            flat_path = os.path.join(self.config_dir, key)
            if os.path.exists(flat_path):
                try:
                    with open(flat_path, 'r', encoding='utf-8') as f:
                        val = f.readline().strip()
                    if val:
                        merged[key] = val
                except OSError:
                    pass

        # If API key in files is empty or masked as [KEYRING], attempt retrieval from system keyring
        if not merged.get('translate_api_key') or merged.get('translate_api_key') == '[KEYRING]':
            kr_key = get_keyring_api_key()
            if kr_key:
                merged['translate_api_key'] = kr_key

        return self.validate(merged)

    def get(self, key: str, default: Any = None) -> Any:
        """Convenience accessor to get a single validated setting value."""
        return self.load().get(key, default)

    def save(self, updates: Dict[str, Any]) -> Dict[str, str]:
        """Validate and atomically write both settings.json and flat key files (with trailing newline)."""
        current = self.load()
        current.update(updates or {})
        validated = self.validate(current)

        # Store sensitive API key in system keyring if available
        if 'translate_api_key' in (updates or {}):
            store_keyring_api_key(validated.get('translate_api_key', ''))

        atomic_write_json(self.json_path, validated, mode=0o600)
        for key, val in validated.items():
            flat_path = os.path.join(self.config_dir, key)
            atomic_write_text(flat_path, f'{val}\n', mode=0o600)
        for legacy_key, schema_key in self.LEGACY_KEY_MAP.items():
            lpath = os.path.join(self.config_dir, legacy_key)
            atomic_write_text(lpath, f'{validated[schema_key]}\n', mode=0o600)
        return validated

# ── Regexes ───────────────────────────────────────────────────────────────────

BIDI_TRANSLATE = {
    0x200E: None, 0x200F: None,
    0x202A: None, 0x202B: None, 0x202C: None, 0x202D: None, 0x202E: None,
    0x2066: None, 0x2067: None, 0x2068: None, 0x2069: None,
}

CTRL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

ARABIC_RUN_RE = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\s]*'
)

GERMAN_CHARS_RE = re.compile(r'[äöüßÄÖÜẞ]')

FALLBACK_DE_WORDS = {
    'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'und', 'mit',
    'zu', 'von', 'bei', 'nach', 'ist', 'sind', 'du', 'ich', 'wir', 'nicht',
    'an', 'am', 'im', 'zum', 'zur', 'für', 'oder', 'aber', 'wenn', 'dass',
    'wird', 'werden', 'haben', 'kann', 'soll', 'muss', 'ja', 'nein', 'auch',
    'auf', 'für', 'über', 'unter', 'durch', 'gegen', 'ohne', 'um',
}

ABBREV_RE = re.compile(
    r'\b(?:Dr|Mr|Mrs|Ms|Prof|Jr|Sr|vs|etc|e\.g|i\.e|Inc|Ltd|Corp|No|no|fig|Fig|vol|pg|p|sec|approx|min|max)\.'
)

SENT_SPLIT_RE = re.compile(r'(?<=[.!?؟。])\s+')
CLAUSE_SPLIT_RE = re.compile(r'(?<=[;؛:])\s+')
COMMA_SPLIT_RE = re.compile(r'(?<=[,،])\s+')
PUNCT_PAUSE_RE = re.compile(r'(?<=[,;:!?،؛؟。])\s+|(?<=\.)\s+(?!\d)')


def split_text_into_sentences(text: str) -> List[str]:
    """Split clean text into individual sentence strings."""
    if not text:
        return []
    return [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]


def to_bool(val: str, default: bool = False) -> bool:
    if val is None or val == '':
        return default
    return str(val).strip().lower() in ('yes', 'true', '1', 'on')


def speakable_count(s: str) -> int:
    return sum(1 for ch in s if unicodedata.category(ch)[0] in ('L', 'N'))


def clean_and_filter_text(
    text: str,
    filter_markdown: bool = True,
    filter_urls: bool = True,
    filter_code: bool = False,
    replace_symbols: bool = True,
    use_lexicon: bool = True,
    lexicon: Optional[Dict[str, str]] = None,
    max_chars: int = 20000,
) -> str:
    """Clean control characters, markdown formatting, URLs, technical symbols, and lexicon aliases."""
    if not text:
        return ''

    # 1. Strip ASCII control chars & Unicode bidi marks
    text = CTRL_CHARS_RE.sub('', text)
    text = text.translate(BIDI_TRANSLATE)

    if filter_markdown:
        # Strip YAML frontmatter
        text = re.sub(r'^(?:---|\.\.\.)\n[\s\S]*?\n(?:---|\.\.\.)\n?', '', text)
        # Strip HTML & Obsidian comments
        text = re.sub(r'<!--[\s\S]*?-->', ' ', text)
        text = re.sub(r'%%[\s\S]*?%%', ' ', text)
        # Fenced code blocks
        if filter_code:
            text = re.sub(r'```[\s\S]*?```', ' ', text)
        else:
            text = re.sub(r'```[a-zA-Z0-9_-]*\n?', ' ', text)
        # Inline code backticks
        text = text.replace('`', '')
        # Images ![alt](url) -> alt
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
        # Markdown links [label](url) -> label
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # Obsidian wikilinks [[target|label]] -> label, [[target]] -> target
        text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', r'\2', text)
        text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
        # Strip standalone HTML tags while preserving recognized SSML markup tags
        def _strip_html_keep_ssml(m):
            tag_str = m.group(0)
            tag_match = re.match(r'</?([a-zA-Z0-9_:-]+)', tag_str)
            if tag_match and tag_match.group(1).lower() in SSML_TAGS:
                return tag_str
            return ' '

        text = re.sub(r'</?[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?>', _strip_html_keep_ssml, text)
        # Strip Markdown table separator lines
        text = re.sub(r'(?m)^\s*\|?[\s:-]+\|[\s|:-]*$', '', text)
        text = text.replace('|', ' , ')
        # Strip heading markers, blockquotes, bold/italic/strikethrough
        text = re.sub(r'(?m)^\s*#{1,6}\s+', '', text)
        text = re.sub(r'(?m)^\s*>\s+', '', text)
        text = re.sub(r'(\*\*|__|~~)', '', text)
        text = re.sub(r'(?<!\w)\*([^\s*][^*]*?)\*(?!\w)', r'\1', text)

    if filter_urls:
        text = re.sub(r'https?://\S+', ' ', text)
        text = re.sub(r'www\.\S+', ' ', text)

    if replace_symbols:
        # Dimension / multiplication between numbers: 320 x 240 -> 320 by 240
        text = re.sub(r'(\d)\s*[×x]\s*(\d)', r'\1 by \2', text)
        # Comparison and math symbols
        text = re.sub(r'(\w)\s*(?:>=|≥)\s*(\w)', r'\1 greater than or equal to \2', text)
        text = re.sub(r'(\w)\s*(?:<=|≤)\s*(\w)', r'\1 less than or equal to \2', text)
        text = re.sub(r'(\w)\s*(?:!=|≠)\s*(\w)', r'\1 not equal to \2', text)
        text = text.replace('≈', ' approximately ')
        text = re.sub(r'~(?=\d)', ' approximately ', text)
        text = text.replace('→', ' to ').replace('->', ' to ')
        text = text.replace('←', ' from ').replace('<-', ' from ')

    if use_lexicon:
        text = apply_lexicon(text, lexicon=lexicon)

    # Normalize bullets and list items into clean sentence breaks so bullet lists split naturally
    text = re.sub(r'[•◦▪▸►●○]', '. ', text)
    text = re.sub(r'(?m)^\s*[-*+]\s+', '', text)

    # Convert line breaks that don't end with punctuation into sentence boundaries
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append('\n\n')
            continue
        if line[-1] not in '.!?;:،؛؟。':
            line = line + '.'
        lines.append(line)
    text = ' '.join(lines)
    text = re.sub(r'\s*\n\s*\n\s*', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text).strip()

    if len(text) > max_chars:
        text = text[:max_chars]

    return text


clean_text_for_tts = clean_and_filter_text


def _has_umlaut(text: str) -> bool:
    return bool(GERMAN_CHARS_RE.search(text))


def _fallback_detect(text: str) -> str:
    words = re.findall(r'\b\w+\b', text.lower())
    if not words:
        return 'en'
    de_count = sum(1 for w in words if w in FALLBACK_DE_WORDS)
    if de_count >= max(1, len(words) // 4):
        return 'de'
    return 'en'


def classify_non_arabic_block(chunk: str):
    chunk = chunk.strip()
    if not chunk:
        return []

    sentences = [s.strip() for s in SENT_SPLIT_RE.split(chunk) if s.strip()]
    if not sentences:
        sentences = [chunk]

    results = []
    for sent in sentences:
        if _has_umlaut(sent):
            results.append(('de', sent))
            continue

        words = re.findall(r'\b\w+\b', sent)
        if len(words) < 4:
            results.append((_fallback_detect(sent), sent))
            continue

        try:
            from langdetect import DetectorFactory, detect_langs
            DetectorFactory.seed = 0
            probs = detect_langs(sent)
            de_prob = next((p.prob for p in probs if p.lang == 'de'), 0.0)
            lower_words = [w.lower() for w in words]
            has_de_word = any(w in FALLBACK_DE_WORDS for w in lower_words)
            # Require at least one German function word so English technical jargon
            # ("Gaussian blur, Sobel gradient") is never misclassified as German.
            is_de = de_prob >= 0.70 and has_de_word
            results.append(('de' if is_de else 'en', sent))
        except Exception:
            results.append((_fallback_detect(sent), sent))

    merged = []
    for lang, t in results:
        if merged and merged[-1][0] == lang:
            merged[-1] = (lang, merged[-1][1] + ' ' + t)
        else:
            merged.append((lang, t))
    return merged


def is_non_letter_chunk(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    for ch in s:
        if unicodedata.category(ch)[0] == 'L':
            return False
    return any(unicodedata.category(ch)[0] == 'N' for ch in s)


def sanitize_for_voice(lang: str, text: str) -> str:
    if lang == 'ar':
        return re.sub(
            r'[^\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\s\d.,;:!?\'"\-–—()\[\]%+/=]+',
            ' ',
            text,
        )
    return re.sub(
        r'[^\u0000-\u024F\u1E00-\u1EFF\u2000-\u206F\s\d.,;:!?\'"\-–—()\[\]%+/=]+'
        r'|\u200B|\u200C|\u200D|\uFEFF'
        r'|[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF]',
        ' ',
        text,
    )


def segment_by_language(text: str, source_lang: str = 'auto'):
    source_lang = (source_lang or 'auto').strip().lower()
    if source_lang not in ('en', 'ar', 'de'):
        source_lang = 'auto'
    if source_lang != 'auto':
        cleaned = sanitize_for_voice(source_lang, text).strip()
        if speakable_count(cleaned) >= 2:
            return [(source_lang, cleaned)]
        return [(source_lang, text.strip())] if speakable_count(text) >= 2 else []

    # Pass 1: Arabic script runs
    raw_segs, last = [], 0
    for m in ARABIC_RUN_RE.finditer(text):
        if m.start() > last:
            chunk = text[last:m.start()].strip()
            if chunk:
                raw_segs.append(chunk)
        ar_chunk = m.group().strip()
        if ar_chunk:
            raw_segs.append(('ar', ar_chunk))
        last = m.end()
    if last < len(text):
        chunk = text[last:].strip()
        if chunk:
            raw_segs.append(chunk)
    if not raw_segs:
        raw_segs.append(text)

    # Pass 2: Classify non-Arabic runs as EN or DE
    segs = []
    for item in raw_segs:
        if isinstance(item, tuple):
            segs.append(item)
        else:
            chunk = item.strip()
            if chunk:
                segs.extend(classify_non_arabic_block(chunk))

    # Merge digit-only runs into adjacent Arabic runs
    merged_digits = []
    i = 0
    while i < len(segs):
        lang, chunk = segs[i]
        if lang in ('en', 'de') and is_non_letter_chunk(chunk):
            if merged_digits and merged_digits[-1][0] == 'ar':
                pl, pc = merged_digits[-1]
                merged_digits[-1] = ('ar', pc + ' ' + chunk)
            elif i + 1 < len(segs) and segs[i + 1][0] == 'ar':
                nl, nc = segs[i + 1]
                segs[i + 1] = ('ar', chunk + ' ' + nc)
            else:
                merged_digits.append((lang, chunk))
        else:
            merged_digits.append((lang, chunk))
        i += 1
    segs = merged_digits

    # Merge consecutive same-language segments
    merged, last_lang, acc = [], None, ''
    for lang, chunk in segs:
        stripped = chunk.strip()
        if not stripped or speakable_count(stripped) < 2:
            continue
        if lang == last_lang:
            acc += ' ' + stripped
        else:
            if last_lang is not None:
                merged.append((last_lang, acc))
            last_lang = lang
            acc = stripped
    if last_lang is not None:
        merged.append((last_lang, acc))

    sanitized = []
    for lang, chunk in merged:
        cleaned = re.sub(r'\s+', ' ', sanitize_for_voice(lang, chunk)).strip()
        if cleaned and speakable_count(cleaned) >= 2:
            sanitized.append((lang, cleaned))

    if not sanitized and speakable_count(text) >= 2:
        try:
            from unidecode import unidecode
            roman = re.sub(r'\s+', ' ', unidecode(text)).strip()
            if speakable_count(roman) >= 2:
                sanitized = [('en', roman)]
        except Exception:
            pass

    return sanitized


def _split_oversized_sentence(sent: str, max_len: int):
    """Break a single long sentence into clause/word pieces <= max_len."""
    if len(sent) <= max_len:
        return [sent]
    pieces = []
    clauses = CLAUSE_SPLIT_RE.split(sent)
    current = ''
    for cl in clauses:
        cl = cl.strip()
        if not cl:
            continue
        if len(cl) > max_len:
            if current:
                pieces.append(current)
                current = ''
            # Try comma split
            sub_clauses = COMMA_SPLIT_RE.split(cl)
            for sc in sub_clauses:
                sc = sc.strip()
                if not sc:
                    continue
                if len(sc) > max_len:
                    if current:
                        pieces.append(current)
                        current = ''
                    # Word split
                    words = sc.split()
                    wbuf = ''
                    for w in words:
                        cand = (wbuf + ' ' + w).strip() if wbuf else w
                        if len(cand) > max_len and wbuf:
                            pieces.append(wbuf)
                            wbuf = w
                        else:
                            wbuf = cand
                    if wbuf:
                        current = wbuf
                else:
                    cand = (current + ', ' + sc).strip() if current else sc
                    if len(cand) <= max_len:
                        current = cand
                    else:
                        if current:
                            pieces.append(current)
                        current = sc
        else:
            cand = (current + ' ' + cl).strip() if current else cl
            if len(cand) <= max_len:
                current = cand
            else:
                if current:
                    pieces.append(current)
                current = cl
    if current:
        pieces.append(current)
    return [p for p in pieces if speakable_count(p) >= 2]


def split_into_chunks(
    lang_segments,
    first_chunk_max: int = 220,
    max_segment_chars: int = 480,
    pause_punctuation: bool = False,
):
    """
    Split language segments into speech chunks.
    - Chunk 0 is capped at `first_chunk_max` (~220 chars) so initial audio starts in <0.8s.
    - Subsequent chunks are capped at `max_segment_chars` (~480 chars) for natural prosody.
    - When `pause_punctuation` is enabled, splits at major punctuation boundaries while
      merging very short fragments (<40 chars) so we never spawn dozens of micro-requests.
    """
    final_chunks = []

    for lang, seg_text in lang_segments:
        # Protect abbreviations
        masked = ABBREV_RE.sub(lambda m: m.group().replace('.', '\x00'), seg_text)

        if pause_punctuation:
            raw_parts = PUNCT_PAUSE_RE.split(masked)
        else:
            raw_parts = []
            for para in masked.split('\n\n'):
                para = para.strip()
                if not para:
                    continue
                raw_parts.extend(SENT_SPLIT_RE.split(para))

        sentences = []
        for p in raw_parts:
            restored = p.replace('\x00', '.').strip()
            if restored and speakable_count(restored) >= 2:
                sentences.append(restored)

        if not sentences and speakable_count(seg_text) >= 2:
            sentences = [seg_text.strip()]

        current = ''
        for sent in sentences:
            target_max = first_chunk_max if (len(final_chunks) == 0 and not current) else max_segment_chars

            if pause_punctuation:
                # In punctuation-pause mode, flush once current reaches at least 45 chars
                if current and len(current) >= 45:
                    final_chunks.append((lang, current))
                    current = ''
                    target_max = max_segment_chars

            if len(sent) > target_max:
                if current:
                    final_chunks.append((lang, current))
                    current = ''
                sub_parts = _split_oversized_sentence(
                    sent,
                    first_chunk_max if len(final_chunks) == 0 else max_segment_chars,
                )
                for sp in sub_parts[:-1]:
                    final_chunks.append((lang, sp))
                if sub_parts:
                    current = sub_parts[-1]
            else:
                cand = (current + ' ' + sent).strip() if current else sent
                limit = first_chunk_max if len(final_chunks) == 0 else max_segment_chars
                if len(cand) <= limit:
                    current = cand
                else:
                    if current:
                        final_chunks.append((lang, current))
                    current = sent

        if current and speakable_count(current) >= 2:
            final_chunks.append((lang, current))

    return final_chunks


def derive_title(text: str, max_len: int = 48) -> str:
    """Create a clean human-readable title from the first line/sentence."""
    first_line = text.strip().splitlines()[0] if text.strip() else 'Speech'
    first_line = re.sub(r'\s+', ' ', first_line).strip(' .,-:;!?"\'')
    if not first_line:
        return 'Selected Text'
    if len(first_line) > max_len:
        return first_line[:max_len - 1].rstrip() + '…'
    return first_line


def process_and_write_segments(
    raw_text: str,
    workdir: str,
    source_lang: str = 'auto',
    pause_punctuation: bool = False,
    first_chunk_max: int = 220,
    max_segment_chars: int = 480,
    filter_markdown: bool = True,
    filter_urls: bool = True,
    filter_code: bool = False,
    replace_symbols: bool = True,
    use_lexicon: bool = True,
) -> int:
    os.makedirs(workdir, mode=0o700, exist_ok=True)
    ensure_runtime_symlinks()

    cleaned_text = clean_and_filter_text(
        raw_text,
        filter_markdown=filter_markdown,
        filter_urls=filter_urls,
        filter_code=filter_code,
        replace_symbols=replace_symbols,
        use_lexicon=use_lexicon,
    )

    if speakable_count(cleaned_text) < 2:
        return 0

    lang_segs = segment_by_language(cleaned_text, source_lang=source_lang)
    if not lang_segs:
        return 0

    chunks = split_into_chunks(
        lang_segs,
        first_chunk_max=first_chunk_max,
        max_segment_chars=max_segment_chars,
        pause_punctuation=pause_punctuation,
    )
    if not chunks:
        return 0

    manifest_chunks = []
    settings_data = TTSSettingsStore().load()
    for idx, (lang, chunk_text) in enumerate(chunks):
        pad = f'{idx:03d}'
        rate_key = f'rate_{lang}'
        seg_rate = settings_data.get(rate_key, settings_data.get('rate_en', '+0%'))
        rate_mult = parse_rate_multiplier(seg_rate)

        with open(os.path.join(workdir, f'seg_{pad}.txt'), 'w', encoding='utf-8') as f:
            f.write(chunk_text)
        with open(os.path.join(workdir, f'seg_{pad}.lang'), 'w', encoding='utf-8') as f:
            f.write(lang)
        with open(os.path.join(workdir, f'seg_{pad}.rate'), 'w', encoding='utf-8') as f:
            f.write(seg_rate)
        with open(os.path.join(workdir, f'seg_{pad}.status'), 'w', encoding='utf-8') as f:
            f.write('pending')

        est_dur = round(max(1.2, (len(chunk_text) / 14.5) / max(0.5, rate_mult)), 2)
        manifest_chunks.append({
            'id': idx,
            'pad': pad,
            'lang': lang,
            'rate': seg_rate,
            'rate_multiplier': rate_mult,
            'text': chunk_text,
            'chars': len(chunk_text),
            'est_duration': est_dur,
        })

    manifest = {
        'session_id': f'{time.time():.3f}',
        'title': derive_title(cleaned_text),
        'total_chunks': len(chunks),
        'created_at': time.time(),
        'chunks': manifest_chunks,
    }
    tmp_manifest = os.path.join(workdir, 'manifest.json.tmp')
    final_manifest = os.path.join(workdir, 'manifest.json')
    with open(tmp_manifest, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp_manifest, final_manifest)

    return len(chunks)


def main():
    parser = argparse.ArgumentParser(description='Process and segment text for TTS.')
    parser.add_argument('--workdir', default='', help='Work directory for segment files')
    parser.add_argument('--print-runtime-dir', action='store_true', help='Print per-user runtime dir and exit')
    parser.add_argument('--sync-settings', action='store_true', help='Validate and sync ~/.config/tts_settings')
    parser.add_argument('--source-lang', default='auto', help='Source language code or auto')
    parser.add_argument('--pause-punctuation', default='no', help='yes/no')
    parser.add_argument('--first-chunk-max', type=int, default=220)
    parser.add_argument('--max-segment-chars', type=int, default=480)
    parser.add_argument('--filter-markdown', default='yes')
    parser.add_argument('--filter-urls', default='yes')
    parser.add_argument('--filter-code', default='no')
    parser.add_argument('--replace-symbols', default='yes')
    parser.add_argument('--use-lexicon', default='yes')
    args = parser.parse_args()

    if args.print_runtime_dir:
        ensure_runtime_symlinks()
        print(get_runtime_dir())
        return

    if args.sync_settings:
        store = TTSSettingsStore()
        store.save({})
        return

    workdir = args.workdir or get_work_dir()
    raw_text = sys.stdin.read()
    count = process_and_write_segments(
        raw_text=raw_text,
        workdir=workdir,
        source_lang=args.source_lang,
        pause_punctuation=to_bool(args.pause_punctuation, False),
        first_chunk_max=args.first_chunk_max,
        max_segment_chars=args.max_segment_chars,
        filter_markdown=to_bool(args.filter_markdown, True),
        filter_urls=to_bool(args.filter_urls, True),
        filter_code=to_bool(args.filter_code, False),
        replace_symbols=to_bool(args.replace_symbols, True),
        use_lexicon=to_bool(args.use_lexicon, True),
    )
    print(count)


if __name__ == '__main__':
    main()
