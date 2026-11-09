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
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any, Dict, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import (
    CONFIG_DIR,
    _format_rate_str,
    ensure_runtime_symlinks,
    get_synth_socket_path,
)


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
    try:
        proc = subprocess.run(
            [piper_bin, '--model', model_path, '--output_file', tmp_wav],
            input=text.encode('utf-8'),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
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
    piper_model: str = '',
    retries: int = 2,
) -> Dict[str, Any]:
    """Synthesize a segment via edge_tts.Communicate with automatic Piper fallback."""
    if not text or not text.strip():
        return {'ok': False, 'error': 'empty_text'}

    # Normalize rate & voice so edge_tts.Communicate never raises ValueError
    norm_rate = _format_rate_str(rate) if rate else '+0%'
    norm_voice = (voice or 'en-US-ChristopherNeural').splitlines()[0].strip()
    norm_lang = (lang or 'en').strip().lower()
    if norm_lang not in ('en', 'ar', 'de'):
        norm_lang = 'en'

    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, mode=0o700, exist_ok=True)

    last_err = ''
    if _ensure_edge_tts_importable():
        import edge_tts

        for attempt in range(max(1, retries)):
            fd, tmp_out = tempfile.mkstemp(prefix='.edge_', suffix='.mp3', dir=parent)
            os.close(fd)
            try:
                comm = edge_tts.Communicate(
                    text=text,
                    voice=norm_voice,
                    rate=norm_rate,
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
            if attempt + 1 < retries:
                await asyncio.sleep(0.35)

    # Fallback to local offline Piper neural TTS if available
    loop = asyncio.get_running_loop()
    piper_ok = await loop.run_in_executor(
        None,
        synthesize_with_piper,
        text,
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


class AsyncSynthServer:
    """Persistent UNIX domain socket server for zero-startup-overhead TTS synthesis."""

    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or get_synth_socket_path()
        self._server: Optional[asyncio.AbstractServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not raw:
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
        self._server = await asyncio.start_unix_server(self._handle_client, path=self.socket_path)
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
