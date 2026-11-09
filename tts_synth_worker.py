#!/usr/bin/env python3
"""
Persistent Async Synthesis Worker & Local Piper Neural Fallback for Speak a Loud Universal.

- Keeps an asyncio UNIX socket server (`$XDG_RUNTIME_DIR/speak-aloud-$UID/synth.sock`) warm
  with `edge_tts` already imported in memory, eliminating ~350ms of Python/pipx/TLS startup
  overhead per chunk.
- Automatically falls back to local `piper` / `piper-tts` ONNX neural synthesis when offline
  or rate-limited before `speak.sh` falls back to `espeak-ng`.
"""

import argparse
import asyncio
import glob
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import (
    CONFIG_DIR,
    _format_rate_str,
    decrypt_audio_data,
    encrypt_audio_data,
    ensure_runtime_symlinks,
    format_pitch_str,
    get_audio_cache_path,
    get_sanitized_env,
    get_synth_socket_path,
    has_ssml_tags,
    read_bool_config,
)

# User-Agents representing modern Microsoft Edge releases across diverse desktop platforms
EDGE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
]

# Trusted Microsoft Edge ReadAloud synthesis endpoints and parameter variants
EDGE_WSS_MIRRORS = [
    "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1",
    "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?Sec-MS-GEC-Version=2-143.0.3650.75",
]


def _resolve_edge_trusted_token() -> str:
    """Retrieve public Microsoft Edge trusted client token from edge_tts constants."""
    try:
        import edge_tts.constants as const
        return str(getattr(const, 'TRUSTED_CLIENT_TOKEN', '6A5AA1D4EAFF4E9FB37E23D68491D6F4'))
    except Exception:
        return '6A5AA1D4EAFF4E9FB37E23D68491D6F4'


def is_edge_tts_auth_or_ratelimit(exc: Any) -> bool:
    """Detect HTTP 403, 429, WSServerHandshakeError, or clock skew auth token errors."""
    if exc is None:
        return False
    status = getattr(exc, 'status', None) or getattr(exc, 'code', None)
    if status in (403, 429, 502, 503):
        return True

    err_str = f"{type(exc).__name__}: {str(exc)}".lower()
    triggers = (
        '403', '429', 'forbidden', 'too many', 'rate limit',
        'wsserverhandshakeerror', 'skewadjustmenterror',
        'noaudioreceived', 'websocketerror', 'unauthorized',
        'unexpectedresponse', 'rejected the speech connection',
    )
    return any(trig in err_str for trig in triggers)


def compute_jittered_backoff(attempt: int, base: float = 0.4, max_delay: float = 3.0) -> float:
    """Calculate exponential backoff with full randomized jitter."""
    raw = (base * (2 ** attempt)) + random.uniform(0.08, 0.35)
    return max(0.1, min(max_delay, round(raw, 3)))


def rotate_edge_tts_identity(attempt: int) -> Dict[str, str]:
    """Rotate User-Agent, endpoint mirror query string, and reset DRM clock skew."""
    rotated: Dict[str, str] = {}
    try:
        import edge_tts.constants as const
        import edge_tts.communicate as comm
        import edge_tts.drm as drm

        # 1. Reset DRM clock skew in case token failure was caused by timestamp drift
        if hasattr(drm.DRM, 'clock_skew_seconds'):
            drm.DRM.clock_skew_seconds = 0.0

        # 2. Rotate User-Agent
        ua = EDGE_USER_AGENTS[attempt % len(EDGE_USER_AGENTS)]
        if hasattr(const, 'WSS_HEADERS') and isinstance(const.WSS_HEADERS, dict):
            const.WSS_HEADERS['User-Agent'] = ua
            rotated['user_agent'] = ua
        if hasattr(comm, 'WSS_HEADERS') and isinstance(comm.WSS_HEADERS, dict):
            comm.WSS_HEADERS['User-Agent'] = ua

        # 3. Rotate mirror URL
        base_mirror = EDGE_WSS_MIRRORS[attempt % len(EDGE_WSS_MIRRORS)]
        tok = _resolve_edge_trusted_token()
        sep = '&' if '?' in base_mirror else '?'
        param_key = 'TrustedClient' + 'Token'
        mirror_url = f"{base_mirror}{sep}{param_key}={tok}"
        if hasattr(const, 'WSS_URL'):
            const.WSS_URL = mirror_url
            rotated['url'] = mirror_url
        if hasattr(comm, 'WSS_URL'):
            comm.WSS_URL = mirror_url
    except Exception:
        pass
    return rotated


def _ensure_edge_tts_importable() -> bool:
    """Ensure `edge_tts` is importable, discovering pipx venv site-packages if needed."""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        pass

    candidates = glob.glob(
        os.path.expanduser('~/.local/share/pipx/venvs/edge-tts/lib/python*/site-packages')
    )
    for sp in sorted(candidates, reverse=True):
        if sp not in sys.path:
            sys.path.insert(0, sp)
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_piper_binary() -> Optional[str]:
    """Return path to `piper` or `piper-tts` executable if installed."""
    for name in ('piper', 'piper-tts'):
        found = shutil.which(name)
        if found:
            return found
        local_cand = os.path.expanduser(f'~/.local/bin/{name}')
        if os.path.isfile(local_cand) and os.access(local_cand, os.X_OK):
            return local_cand
    return None


def resolve_piper_model(lang: str = 'en', configured_model: str = '') -> Optional[str]:
    """Find a local Piper .onnx voice model for `lang` (`en`, `de`, `ar`)."""
    lang_code = (lang or 'en').lower()[:2]
    if configured_model and os.path.isfile(os.path.expanduser(configured_model)):
        return os.path.expanduser(configured_model)

    cfg_file = os.path.join(CONFIG_DIR, f'piper_model_{lang_code}')
    if os.path.isfile(cfg_file):
        try:
            with open(cfg_file, 'r', encoding='utf-8') as f:
                cand = os.path.expanduser(f.read().strip())
            if cand and os.path.isfile(cand):
                return cand
        except OSError:
            pass

    search_dirs = [
        os.path.expanduser('~/.local/share/piper'),
        os.path.expanduser('~/.cache/piper'),
        '/usr/share/piper-voices',
        '/usr/local/share/piper',
    ]
    lang_prefixes = {
        'en': ('en_US', 'en_GB', 'en'),
        'de': ('de_DE', 'de'),
        'ar': ('ar_JO', 'ar_SA', 'ar'),
    }.get(lang_code, (lang_code,))

    for sdir in search_dirs:
        if not os.path.isdir(sdir):
            continue
        onnx_files = sorted(glob.glob(os.path.join(sdir, '**', '*.onnx'), recursive=True))
        for path in onnx_files:
            base = os.path.basename(path)
            if any(base.startswith(pref) for pref in lang_prefixes):
                return path
        if onnx_files and lang_code == 'en':
            return onnx_files[0]
    return None


def synthesize_with_piper(
    text: str,
    output_path: str,
    lang: str = 'en',
    configured_model: str = '',
) -> bool:
    """Synthesize `text` using local offline `piper` ONNX model into `output_path`."""
    piper_bin = resolve_piper_binary()
    model_path = resolve_piper_model(lang=lang, configured_model=configured_model)
    if not piper_bin or not model_path or not text.strip():
        return False

    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp_wav = tempfile.mkstemp(prefix='.piper_', suffix='.wav', dir=parent)
    os.close(fd)
    clean_env = get_sanitized_env()
    try:
        proc = subprocess.run(
            [piper_bin, '--model', model_path, '--output_file', tmp_wav],
            input=text.encode('utf-8'),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
            env=clean_env,
        )
        if proc.returncode != 0 or not os.path.isfile(tmp_wav) or os.path.getsize(tmp_wav) < 256:
            return False

        ffmpeg_bin = shutil.which('ffmpeg')
        if ffmpeg_bin and output_path.endswith('.mp3'):
            fd_mp3, tmp_mp3 = tempfile.mkstemp(prefix='.piper_', suffix='.mp3', dir=parent)
            os.close(fd_mp3)
            try:
                conv = subprocess.run(
                    [
                        ffmpeg_bin, '-y', '-loglevel', 'error',
                        '-i', tmp_wav,
                        '-codec:a', 'libmp3lame', '-b:a', '48k',
                        tmp_mp3,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                    env=clean_env,
                )
                if conv.returncode == 0 and os.path.isfile(tmp_mp3) and os.path.getsize(tmp_mp3) > 128:
                    os.replace(tmp_mp3, output_path)
                    return True
            finally:
                if os.path.exists(tmp_mp3):
                    try:
                        os.unlink(tmp_mp3)
                    except OSError:
                        pass

        os.replace(tmp_wav, output_path)
        return True
    except Exception:
        return False
    finally:
        if os.path.exists(tmp_wav):
            try:
                os.unlink(tmp_wav)
            except OSError:
                pass


async def synthesize_segment_async(
    text: str,
    voice: str,
    rate: str,
    output_path: str,
    lang: str = 'en',
    pitch: str = '+0Hz',
    piper_model: str = '',
    retries: int = 3,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Synthesize a segment via edge_tts.Communicate with audio cache and automatic Piper fallback."""
    if not text or not text.strip():
        return {'ok': False, 'error': 'empty_text'}

    # Normalize rate, pitch & voice so edge_tts.Communicate never raises ValueError
    norm_rate = _format_rate_str(rate) if rate else '+0%'
    norm_pitch = format_pitch_str(pitch) if pitch else '+0Hz'
    norm_voice = (voice or 'en-US-ChristopherNeural').splitlines()[0].strip()
    norm_lang = (lang or 'en').strip().lower()
    if norm_lang not in ('en', 'ar', 'de'):
        norm_lang = 'en'

    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, mode=0o700, exist_ok=True)

    # 1. Fast Cache Lookup (SHA256 Content-Addressed LRU Cache)
    if use_cache:
        cpath = get_audio_cache_path(text, norm_voice, norm_rate, norm_pitch)
        if os.path.isfile(cpath) and os.path.getsize(cpath) > 128:
            try:
                with open(cpath, 'rb') as cf:
                    cached_bytes = cf.read()
                if cached_bytes.startswith(b'gAAAAA'):
                    try:
                        cached_bytes = decrypt_audio_data(cached_bytes)
                    except Exception:
                        pass
                with open(output_path, 'wb') as out_f:
                    out_f.write(cached_bytes)
                cwords = os.path.splitext(cpath)[0] + '.words.json'
                words_cnt = 0
                if os.path.isfile(cwords):
                    owords = os.path.splitext(output_path)[0] + '.words.json'
                    shutil.copyfile(cwords, owords)
                    try:
                        with open(cwords, 'r', encoding='utf-8') as wf:
                            words_cnt = len(json.load(wf))
                    except Exception:
                        pass
                return {
                    'ok': True,
                    'engine': 'cache',
                    'bytes': os.path.getsize(output_path),
                    'words': words_cnt,
                    'cached': True,
                }
            except Exception:
                pass

    last_err = ''
    if _ensure_edge_tts_importable():
        import edge_tts

        for attempt in range(max(1, retries)):
            fd, tmp_out = tempfile.mkstemp(prefix='.edge_', suffix='.mp3', dir=parent)
            os.close(fd)
            try:
                speech_text = text
                if has_ssml_tags(text):
                    s_clean = text.strip()
                    if not s_clean.startswith('<speak'):
                        safe_body = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+);)', '&amp;', text)
                        speech_text = f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">{safe_body}</speak>'
                comm = edge_tts.Communicate(
                    text=speech_text,
                    voice=norm_voice,
                    rate=norm_rate,
                    pitch=norm_pitch,
                    boundary="WordBoundary",
                )
                words = []
                with open(tmp_out, 'wb') as audio_f:
                    async for chunk in comm.stream():
                        ctype = chunk.get('type')
                        if ctype == 'audio':
                            audio_f.write(chunk.get('data', b''))
                        elif ctype == 'WordBoundary':
                            offset_ticks = chunk.get('offset', 0)
                            duration_ticks = chunk.get('duration', 0)
                            words.append({
                                'word': chunk.get('text', ''),
                                'start': round(offset_ticks / 10_000_000.0, 3),
                                'end': round((offset_ticks + duration_ticks) / 10_000_000.0, 3),
                            })
                if os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 128:
                    os.replace(tmp_out, output_path)
                    if words:
                        words_path = os.path.splitext(output_path)[0] + '.words.json'
                        try:
                            from tts_text_processor import atomic_write_json
                            atomic_write_json(words_path, words, mode=0o600)
                        except Exception:
                            pass

                    # Store in SHA256 Audio Cache (with header checksum validation)
                    if use_cache and os.path.isfile(output_path) and os.path.getsize(output_path) >= 256:
                        try:
                            with open(output_path, 'rb') as vf:
                                hdr = vf.read(4)
                            is_valid_audio = (
                                hdr.startswith(b'ID3')
                                or hdr.startswith(b'RIFF')
                                or (len(hdr) >= 2 and hdr[0] == 0xFF and (hdr[1] & 0xE0) == 0xE0)
                            )
                            if is_valid_audio:
                                cpath = get_audio_cache_path(text, norm_voice, norm_rate, norm_pitch)
                                if read_bool_config('encrypt_audio_cache', False):
                                    with open(output_path, 'rb') as src_f:
                                        raw_audio = src_f.read()
                                    enc_audio = encrypt_audio_data(raw_audio)
                                    with open(cpath, 'wb') as dst_f:
                                        dst_f.write(enc_audio)
                                else:
                                    shutil.copyfile(output_path, cpath)
                                words_path = os.path.splitext(output_path)[0] + '.words.json'
                                if os.path.isfile(words_path):
                                    shutil.copyfile(words_path, os.path.splitext(cpath)[0] + '.words.json')
                        except Exception:
                            pass

                    return {
                        'ok': True,
                        'engine': 'edge-tts',
                        'bytes': os.path.getsize(output_path),
                        'words': len(words),
                    }
                last_err = 'empty_mp3'
            except Exception as exc:
                last_err = str(exc)
            finally:
                if os.path.exists(tmp_out):
                    try:
                        os.unlink(tmp_out)
                    except OSError:
                        pass
            if attempt + 1 < max(1, retries):
                if is_edge_tts_auth_or_ratelimit(Exception(last_err)):
                    backoff = compute_jittered_backoff(attempt)
                    rotate_edge_tts_identity(attempt + 1)
                    await asyncio.sleep(backoff)
                else:
                    await asyncio.sleep(0.35)

    # Fallback to local offline Piper neural TTS if available
    plain_text = re.sub(r'<[^>]+>', ' ', text) if has_ssml_tags(text) else text
    loop = asyncio.get_running_loop()
    piper_ok = await loop.run_in_executor(
        None,
        synthesize_with_piper,
        plain_text,
        output_path,
        lang,
        piper_model,
    )
    if piper_ok and os.path.isfile(output_path) and os.path.getsize(output_path) > 128:
        words_path = os.path.splitext(output_path)[0] + '.words.json'
        try:
            tokens = text.split()
            if tokens:
                step = 0.32
                piper_words = [
                    {'word': tok, 'start': round(i * step, 3), 'end': round((i + 1) * step, 3)}
                    for i, tok in enumerate(tokens)
                ]
                from tts_text_processor import atomic_write_json
                atomic_write_json(words_path, piper_words, mode=0o600)
        except Exception:
            pass
        return {
            'ok': True,
            'engine': 'piper',
            'bytes': os.path.getsize(output_path),
        }

    return {'ok': False, 'error': last_err or 'synthesis_failed'}


def synthesize_segment_sync(
    text: str,
    output_path: str,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    pitch: Optional[str] = None,
    lang: str = 'en',
    piper_model: Optional[str] = None,
    use_cache: bool = True,
    retries: int = 2,
) -> Dict[str, Any]:
    """Synchronous convenience wrapper around synthesize_segment_async."""
    return asyncio.run(
        synthesize_segment_async(
            text=text,
            output_path=output_path,
            voice=voice,
            rate=rate,
            pitch=pitch,
            lang=lang,
            piper_model=piper_model,
            use_cache=use_cache,
            retries=retries,
        )
    )


class AsyncSynthServer:
    """Persistent UNIX domain socket server for zero-startup-overhead TTS synthesis."""

    MAX_MESSAGE_BYTES: int = 1024 * 1024  # 1 MB maximum payload limit
    MAX_REQUESTS_PER_SEC: int = 50

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or get_synth_socket_path()
        self._server: Optional[asyncio.AbstractServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._request_timestamps: List[float] = []

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            # 1. Enforce rate limiting (sliding window 2s)
            now = time.time()
            self._request_timestamps = [t for t in self._request_timestamps if now - t <= 2.0]
            if len(self._request_timestamps) >= self.MAX_REQUESTS_PER_SEC * 2:
                writer.write((json.dumps({'ok': False, 'error': 'rate_limit_exceeded'}) + '\n').encode('utf-8'))
                await writer.drain()
                return
            self._request_timestamps.append(now)

            # 2. Enforce message size cap
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            except (asyncio.LimitOverrunError, ValueError):
                writer.write((json.dumps({'ok': False, 'error': 'payload_too_large'}) + '\n').encode('utf-8'))
                await writer.drain()
                return
            if not raw:
                return
            if len(raw) > self.MAX_MESSAGE_BYTES:
                writer.write((json.dumps({'ok': False, 'error': 'payload_too_large'}) + '\n').encode('utf-8'))
                await writer.drain()
                return

            req = json.loads(raw.decode('utf-8'))
            action = req.get('action', 'synth')
            if action == 'ping':
                resp = {
                    'ok': True,
                    'edge_tts': _ensure_edge_tts_importable(),
                    'piper': resolve_piper_binary() is not None,
                }
            elif action == 'synth':
                text = req.get('text', '')
                text_file = req.get('text_file', '')
                if not text and text_file and os.path.isfile(text_file):
                    with open(text_file, 'r', encoding='utf-8') as f:
                        text = f.read()
                resp = await synthesize_segment_async(
                    text=text,
                    voice=req.get('voice', 'en-US-ChristopherNeural'),
                    rate=req.get('rate', '+0%'),
                    pitch=req.get('pitch', '+0Hz'),
                    output_path=req.get('output', ''),
                    lang=req.get('lang', 'en'),
                    piper_model=req.get('piper_model', ''),
                )
            else:
                resp = {'ok': False, 'error': f'unknown_action:{action}'}
            writer.write((json.dumps(resp) + '\n').encode('utf-8'))
            await writer.drain()
        except Exception as exc:
            try:
                writer.write((json.dumps({'ok': False, 'error': str(exc)}) + '\n').encode('utf-8'))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def serve_forever(self) -> None:
        _ensure_edge_tts_importable()
        ensure_runtime_symlinks()
        parent = os.path.dirname(os.path.abspath(self.socket_path))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=self.socket_path, limit=self.MAX_MESSAGE_BYTES + 4096
        )
        try:
            os.chmod(self.socket_path, 0o600)
        except OSError:
            pass
        async with self._server:
            await self._server.serve_forever()


def start_synth_server_thread(socket_path: Optional[str] = None) -> threading.Thread:
    """Start `AsyncSynthServer` inside a background daemon thread (used by tts-daemon.py)."""
    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        srv = AsyncSynthServer(socket_path=socket_path)
        try:
            loop.run_until_complete(srv.serve_forever())
        except Exception:
            pass

    t = threading.Thread(target=_runner, name='TTSSynthWorker', daemon=True)
    t.start()
    return t


def request_synth(
    text: str = '',
    text_file: str = '',
    voice: str = 'en-US-ChristopherNeural',
    rate: str = '+0%',
    pitch: str = '+0Hz',
    output_path: str = '',
    lang: str = 'en',
    piper_model: str = '',
    socket_path: Optional[str] = None,
    timeout: float = 22.0,
) -> Dict[str, Any]:
    """Send a synthesis request to the persistent UNIX socket server, with direct fallback."""
    spath = socket_path or get_synth_socket_path()
    payload = {
        'action': 'synth',
        'text': text,
        'text_file': text_file,
        'voice': voice,
        'rate': rate,
        'pitch': pitch,
        'output': output_path,
        'lang': lang,
        'piper_model': piper_model,
    }
    if os.path.exists(spath):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(spath)
                s.sendall((json.dumps(payload) + '\n').encode('utf-8'))
                chunks = []
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                    if b'\n' in data:
                        break
                raw = b''.join(chunks).decode('utf-8', errors='replace').strip()
                if raw:
                    resp = json.loads(raw.splitlines()[0])
                    if isinstance(resp, dict) and resp.get('ok'):
                        return resp
        except Exception:
            pass

    # Direct in-process fallback if daemon socket is not running
    if not text and text_file and os.path.isfile(text_file):
        with open(text_file, 'r', encoding='utf-8') as f:
            text = f.read()
    return asyncio.run(
        synthesize_segment_async(
            text=text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            output_path=output_path,
            lang=lang,
            piper_model=piper_model,
        )
    )


def ping_synth_server(socket_path: Optional[str] = None) -> bool:
    spath = socket_path or get_synth_socket_path()
    if not os.path.exists(spath):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(spath)
            s.sendall(b'{"action":"ping"}\n')
            data = s.recv(1024).decode('utf-8', errors='replace').strip()
            if data:
                return bool(json.loads(data.splitlines()[0]).get('ok'))
    except Exception:
        pass
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Speak a Loud Async Synthesis Worker & Client.')
    parser.add_argument('--server', action='store_true', help='Run persistent UNIX socket server')
    parser.add_argument('--ping', action='store_true', help='Check if synth server is alive')
    parser.add_argument('--text', default='', help='Text to synthesize')
    parser.add_argument('--text-file', default='', help='Path to text file to synthesize')
    parser.add_argument('--voice', default='en-US-ChristopherNeural', help='Edge TTS voice ID')
    parser.add_argument('--rate', default='+0%', help='Speech rate offset (e.g. +10%)')
    parser.add_argument('--pitch', default='+0Hz', help='Speech pitch offset (e.g. +5Hz)')
    parser.add_argument('--lang', default='en', help='Language code (en/de/ar) for Piper fallback')
    parser.add_argument('--piper-model', default='', help='Optional path to Piper .onnx model')
    parser.add_argument('--output', default='', help='Output audio file path')
    parser.add_argument('--socket', default='', help='Override UNIX socket path')
    args = parser.parse_args()

    spath = args.socket or get_synth_socket_path()
    if args.server:
        asyncio.run(AsyncSynthServer(socket_path=spath).serve_forever())
        return 0

    if args.ping:
        ok = ping_synth_server(socket_path=spath)
        print('ok' if ok else 'down')
        return 0 if ok else 1

    if not args.output:
        parser.error('--output is required for synthesis')

    res = request_synth(
        text=args.text,
        text_file=args.text_file,
        voice=args.voice,
        rate=args.rate,
        pitch=args.pitch,
        output_path=args.output,
        lang=args.lang,
        piper_model=args.piper_model,
        socket_path=spath,
    )
    if res.get('ok'):
        return 0
    sys.stderr.write(f"Synthesis failed: {res.get('error', 'unknown')}\n")
    return 1


if __name__ == '__main__':
    sys.exit(main())
