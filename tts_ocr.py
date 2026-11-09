#!/usr/bin/env python3
"""
OCR Screen-Region & Image Text Extractor for Speak a Loud Universal (`--ocr`).

Features:
- Built-in PyQt6 desktop rubber-band region selector (works natively on Cinnamon/X11
  without requiring `maim` or GNOME Shell D-Bus screenshot services), with automatic
  fallback to `maim -s`, `gnome-screenshot -a`, or `scrot -s`.
- Direct `--image <file>` mode for reading text from any image file or automated tests.
- Auto-detects installed Tesseract language packs (`eng`, `deu`, `ara`).
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import get_runtime_dir


def get_available_tesseract_langs(preferred: Optional[List[str]] = None) -> str:
    """Return `+`-joined Tesseract language string for installed language packs."""
    pref = preferred or ['eng', 'deu', 'ara']
    tess_bin = shutil.which('tesseract')
    if not tess_bin:
        return 'eng'
    try:
        proc = subprocess.run(
            [tess_bin, '--list-langs'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        installed = {
            line.strip()
            for line in (proc.stdout + '\n' + proc.stderr).splitlines()
            if line.strip() and not line.lower().startswith('list of')
        }
        chosen = [lang for lang in pref if lang in installed]
        return '+'.join(chosen) if chosen else 'eng'
    except Exception:
        return 'eng'


def extract_text_from_image(image_path: str, langs: Optional[str] = None) -> str:
    """Run `tesseract` OCR on `image_path` and return extracted text."""
    if not image_path or not os.path.isfile(image_path):
        return ''
    tess_bin = shutil.which('tesseract')
    if not tess_bin:
        raise RuntimeError('tesseract is not installed (sudo apt install tesseract-ocr)')

    lang_arg = langs or get_available_tesseract_langs()
    proc = subprocess.run(
        [tess_bin, image_path, 'stdout', '-l', lang_arg, '--psm', '6'],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        # Retry with default language if multi-lang failed
        proc = subprocess.run(
            [tess_bin, image_path, 'stdout'],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    return (proc.stdout or '').strip()


def capture_region_with_qt(output_png: str) -> bool:
    """Interactive PyQt6 rubber-band screen region capture."""
    if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        return False
    try:
        from PyQt6.QtCore import QPoint, QRect, Qt
        from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPixmap
        from PyQt6.QtWidgets import QApplication, QWidget
    except ImportError:
        return False

    app = QApplication.instance() or QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return False

    full_pixmap: QPixmap = screen.grabWindow(0)
    if full_pixmap.isNull():
        return False

    class RegionOverlay(QWidget):
        def __init__(self, pixmap: QPixmap):
            super().__init__()
            self.pixmap = pixmap
            self.origin: Optional[QPoint] = None
            self.current: Optional[QPoint] = None
            self.captured = False
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setGeometry(screen.geometry())

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self.pixmap)
            painter.fillRect(self.rect(), QColor(15, 17, 24, 120))

            # Top helper pill
            banner = QRect((self.width() - 420) // 2, 24, 420, 36)
            painter.setPen(QPen(QColor(99, 102, 241, 200), 1))
            painter.setBrush(QColor(22, 25, 34, 230))
            painter.drawRoundedRect(banner, 8, 8)
            painter.setPen(QColor(241, 245, 249))
            painter.setFont(QFont('Sans Serif', 10, QFont.Weight.DemiBold))
            painter.drawText(
                banner,
                Qt.AlignmentFlag.AlignCenter,
                'Drag region to read aloud (OCR)  •  Esc to cancel',
            )

            if self.origin and self.current:
                sel = QRect(self.origin, self.current).normalized()
                if sel.width() > 4 and sel.height() > 4:
                    painter.drawPixmap(sel, self.pixmap, sel)
                    painter.setPen(QPen(QColor(99, 102, 241), 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(sel)

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.origin = event.pos()
                self.current = event.pos()
                self.update()
            elif event.button() == Qt.MouseButton.RightButton:
                self.close()

        def mouseMoveEvent(self, event):
            if self.origin is not None:
                self.current = event.pos()
                self.update()

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton and self.origin and self.current:
                sel = QRect(self.origin, self.current).normalized()
                if sel.width() >= 10 and sel.height() >= 10:
                    dpr = self.pixmap.devicePixelRatio()
                    crop_rect = QRect(
                        int(sel.x() * dpr),
                        int(sel.y() * dpr),
                        int(sel.width() * dpr),
                        int(sel.height() * dpr),
                    )
                    cropped = self.pixmap.copy(crop_rect)
                    if not cropped.isNull() and cropped.save(output_png, 'PNG'):
                        self.captured = True
                self.close()

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Escape:
                self.close()

    overlay = RegionOverlay(full_pixmap)
    overlay.showFullScreen()
    app.exec()
    return overlay.captured and os.path.isfile(output_png) and os.path.getsize(output_png) > 0


def capture_region_with_cli(output_png: str) -> bool:
    """Fallback CLI region screenshot tools (`maim`, `gnome-screenshot`, `scrot`)."""
    commands = [
        ['maim', '-s', output_png],
        ['gnome-screenshot', '-a', '-f', output_png],
        ['scrot', '-s', output_png],
    ]
    for cmd in commands:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.run(cmd, timeout=30, check=False)
                if proc.returncode == 0 and os.path.isfile(output_png) and os.path.getsize(output_png) > 0:
                    return True
            except Exception:
                continue
    return False


def capture_and_ocr(image_path: str = '') -> str:
    """Capture a screen region (or use `image_path`) and return OCR text."""
    if image_path:
        return extract_text_from_image(image_path)

    rdir = get_runtime_dir()
    fd, tmp_png = tempfile.mkstemp(prefix='ocr_', suffix='.png', dir=rdir)
    os.close(fd)
    try:
        os.unlink(tmp_png)
        ok = capture_region_with_qt(tmp_png) or capture_region_with_cli(tmp_png)
        if not ok or not os.path.isfile(tmp_png):
            return ''
        return extract_text_from_image(tmp_png)
    finally:
        if os.path.exists(tmp_png):
            try:
                os.unlink(tmp_png)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description='OCR Screen-Region & Image Text Extractor for TTS.')
    parser.add_argument('--image', default='', help='Extract OCR text from an existing image path')
    parser.add_argument('--speak', action='store_true', help='Immediately speak the extracted text via speak.sh')
    parser.add_argument('--queue', action='store_true', help='Queue the extracted text via speak.sh --queue')
    args = parser.parse_args()

    try:
        text = capture_and_ocr(image_path=args.image)
    except Exception as exc:
        sys.stderr.write(f'OCR error: {exc}\n')
        return 1

    if not text or not text.strip():
        return 1

    if args.speak or args.queue:
        speak_sh = os.path.join(SCRIPT_DIR, 'speak.sh')
        cmd = [speak_sh]
        if args.queue:
            cmd.append('--queue')
        cmd.extend(['--text', text])
        subprocess.Popen(cmd, start_new_session=True)
    else:
        sys.stdout.write(text + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
