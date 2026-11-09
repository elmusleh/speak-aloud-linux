#!/usr/bin/env python3
"""
Document & Web Article Reader for Speak a Loud Universal.

Extracts clean spoken text from:
1. PDF documents (using `pdftotext`, single page or whole document)
2. EPUB ebooks (standard python `zipfile` + XHTML spine parser)
3. Web URLs (HTTP article extraction with boilerplate filtering)

Supports speaking immediately or loading chapters/pages directly into
the Playback Queue ($XDG_RUNTIME_DIR/speak-aloud-$UID/queue.json).
"""

import argparse
import html
from html.parser import HTMLParser
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from tts_text_processor import (
    READING_STATE_PATH,
    clean_and_filter_text,
    get_runtime_dir,
    get_sanitized_env,
    load_queue,
    save_queue,
)

logger = logging.getLogger('tts-reader')


def _get_doc_fingerprint(path: str) -> str:
    """Generate persistent fingerprint for a document based on realpath and size."""
    try:
        rpath = os.path.realpath(path)
        st = os.stat(rpath)
        raw = f"{rpath}:{st.st_size}:{st.st_mtime}"
        import hashlib
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
    except OSError:
        import hashlib
        return hashlib.sha256(path.encode('utf-8')).hexdigest()[:16]


def get_reading_state(file_path: str) -> Optional[Dict[str, Any]]:
    """Retrieve saved reading progress for a document from ~/.local/share/speak-aloud/reading_state.json."""
    if not os.path.isfile(READING_STATE_PATH):
        return None
    try:
        with open(READING_STATE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        fp = _get_doc_fingerprint(file_path)
        return data.get(fp)
    except Exception:
        return None


def save_reading_state(file_path: str, chapter: int, total_chapters: int, title: str = '') -> None:
    """Save audiobook / document reading progress atomically."""
    os.makedirs(os.path.dirname(READING_STATE_PATH), mode=0o700, exist_ok=True)
    fp = _get_doc_fingerprint(file_path)
    state = {}
    if os.path.isfile(READING_STATE_PATH):
        try:
            with open(READING_STATE_PATH, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except Exception:
            state = {}
    state[fp] = {
        'file_path': os.path.realpath(file_path),
        'title': title or os.path.basename(file_path),
        'last_chapter': chapter,
        'total_chapters': total_chapters,
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    tmp_path = f"{READING_STATE_PATH}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, READING_STATE_PATH)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class SimpleHTMLTextExtractor(HTMLParser):
    """Clean HTML-to-text parser that strips boilerplate tags (script, style, nav, footer)."""

    SKIP_TAGS = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'svg', 'noscript'}
    BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'article', 'section', 'br'}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._pieces: List[str] = []
        self.title: str = ''
        self._in_title = False

    def handle_starttag(self, tag: str, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
        elif t == 'title':
            self._in_title = True
        elif t in self.BLOCK_TAGS:
            self._pieces.append('\n')

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif t == 'title':
            self._in_title = False
        elif t in self.BLOCK_TAGS:
            self._pieces.append('\n')

    def handle_data(self, data: str):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif self._skip_depth == 0:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = ''.join(self._pieces)
        lines = [line.strip() for line in raw.splitlines()]
        # Collapse multiple blank lines
        cleaned = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        return html.unescape(cleaned)


def extract_from_pdf(pdf_path: str, page: Optional[int] = None) -> List[Dict[str, str]]:
    """Extract text from PDF pages using pdftotext."""
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f'PDF file not found: {pdf_path}')

    pdftotext = shutil.which('pdftotext')
    if not pdftotext:
        raise RuntimeError('pdftotext utility is not installed. Install poppler-utils.')

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    if page is not None and page > 0:
        cmd = [pdftotext, '-f', str(page), '-l', str(page), '-layout', pdf_path, '-']
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=get_sanitized_env())
        txt = clean_and_filter_text(res.stdout)
        return [{'title': f'{base_name} — Page {page}', 'text': txt}]

    # Extract all pages delimited by FormFeed (\x0c)
    cmd = [pdftotext, '-layout', pdf_path, '-']
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, env=get_sanitized_env())
    raw_pages = res.stdout.split('\x0c')
    results: List[Dict[str, str]] = []

    for i, p_raw in enumerate(raw_pages):
        clean_p = clean_and_filter_text(p_raw)
        if len(clean_p.split()) >= 3:
            results.append({
                'title': f'{base_name} — Page {i + 1}',
                'text': clean_p,
            })

    return results


def extract_from_epub(epub_path: str) -> List[Dict[str, str]]:
    """Extract chapters from EPUB container using standard zipfile and xml parser."""
    if not os.path.isfile(epub_path):
        raise FileNotFoundError(f'EPUB file not found: {epub_path}')

    base_name = os.path.splitext(os.path.basename(epub_path))[0]
    results: List[Dict[str, str]] = []

    with zipfile.ZipFile(epub_path, 'r') as z:
        # 1. Parse META-INF/container.xml to find OPF path
        try:
            container_xml = z.read('META-INF/container.xml')
            c_tree = ET.fromstring(container_xml)
            rootfile = c_tree.find('.//{*}rootfile')
            if rootfile is None or 'full-path' not in rootfile.attrib:
                raise ValueError('Invalid EPUB: rootfile not found in container.xml')
            opf_path = rootfile.attrib['full-path']
        except Exception as exc:
            raise ValueError(f'Cannot parse EPUB container: {exc}')

        opf_dir = os.path.dirname(opf_path)
        opf_xml = z.read(opf_path)
        opf_tree = ET.fromstring(opf_xml)

        # 2. Extract manifest items (id -> href)
        manifest: Dict[str, str] = {}
        for item in opf_tree.findall('.//{*}manifest/{*}item'):
            iid = item.attrib.get('id')
            href = item.attrib.get('href')
            if iid and href:
                full_href = os.path.normpath(os.path.join(opf_dir, href)) if opf_dir else href
                manifest[iid] = full_href

        # 3. Follow spine items in reading order
        spine_items = [ref.attrib.get('idref') for ref in opf_tree.findall('.//{*}spine/{*}itemref')]

        chapter_idx = 1
        for iid in spine_items:
            href = manifest.get(iid)
            if not href or href not in z.namelist():
                continue
            try:
                raw_html = z.read(href).decode('utf-8', errors='replace')
                parser = SimpleHTMLTextExtractor()
                parser.feed(raw_html)
                txt = clean_and_filter_text(parser.get_text())
                if len(txt.split()) >= 5:
                    ch_title = parser.title or f'{base_name} — Chapter {chapter_idx}'
                    results.append({
                        'title': ch_title,
                        'text': txt,
                    })
                    chapter_idx += 1
            except Exception as e:
                logger.debug('Skipping EPUB item %s: %s', href, e)

    return results


def extract_from_url(url: str, timeout: float = 10.0) -> Dict[str, str]:
    """Fetch article webpage and extract clean readable text."""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    headers = {
        'User-Agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8,de;q=0.7',
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get('Content-Type', '')
            charset = 'utf-8'
            if 'charset=' in content_type.lower():
                charset = content_type.lower().split('charset=')[-1].split(';')[0].strip()
            raw_html = resp.read().decode(charset, errors='replace')
    except Exception as exc:
        raise RuntimeError(f'Failed to fetch URL {url}: {exc}')

    parser = SimpleHTMLTextExtractor()
    parser.feed(raw_html)
    raw_text = parser.get_text()
    cleaned_text = clean_and_filter_text(raw_text)

    title = parser.title.strip() if parser.title else url
    return {
        'title': title,
        'text': cleaned_text,
        'url': url,
    }


def enqueue_documents(docs: List[Dict[str, str]], source: str = 'reader') -> int:
    """Append extracted document chapters/pages to the Playback Queue."""
    items = load_queue()
    count = 0
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    for d in docs:
        text = d.get('text', '').strip()
        if not text:
            continue
        items.append({
            'id': f'item_{uuid.uuid4().hex[:8]}',
            'title': d.get('title', 'Document Passage')[:90],
            'text': text,
            'source': source,
            'created_at': now,
        })
        count += 1
    if count > 0:
        save_queue(items)
    return count


def main():
    parser = argparse.ArgumentParser(description='Speak a Loud Document & Web Article Reader')
    parser.add_argument('--file', help='Path to PDF or EPUB document')
    parser.add_argument('--page', type=int, help='Page number (PDF only, 1-based)')
    parser.add_argument('--url', help='Webpage URL to extract and read')
    parser.add_argument('--queue', '-q', action='store_true', help='Enqueue extracted items instead of printing')
    parser.add_argument('--speak', action='store_true', help='Trigger speak.sh on the extracted text')
    parser.add_argument('--resume', action='store_true', default=True, help='Resume from last saved reading position')
    parser.add_argument('--reset', action='store_true', help='Reset reading progress and start from chapter 1')

    args = parser.parse_args()

    docs: List[Dict[str, str]] = []

    if args.url:
        doc = extract_from_url(args.url)
        docs.append(doc)
    elif args.file:
        ext = os.path.splitext(args.file)[1].lower()
        if ext == '.pdf':
            docs = extract_from_pdf(args.file, page=args.page)
        elif ext == '.epub':
            docs = extract_from_epub(args.file)
        elif ext in ('.txt', '.md'):
            with open(args.file, 'r', encoding='utf-8', errors='replace') as f:
                content = clean_and_filter_text(f.read())
            base = os.path.basename(args.file)
            docs.append({'title': base, 'text': content})
        else:
            sys.stderr.write(f'Unsupported file format: {ext}\n')
            sys.exit(1)

        # Check and apply reading progress tracker
        if docs and not args.page:
            total_ch = len(docs)
            prev_state = get_reading_state(args.file)
            if prev_state and not args.reset and args.resume:
                last_ch = prev_state.get('last_chapter', 0)
                if 0 < last_ch < total_ch:
                    sys.stderr.write(f"Resuming '{args.file}' from Chapter/Page {last_ch + 1} of {total_ch}...\n")
                    docs = docs[last_ch:]
            save_reading_state(args.file, chapter=0, total_chapters=total_ch, title=docs[0].get('title', ''))
    else:
        parser.print_help()
        sys.exit(1)

    if not docs:
        sys.stderr.write('No readable text content could be extracted.\n')
        sys.exit(1)

    if args.queue:
        added = enqueue_documents(docs, source='file' if args.file else 'web')
        print(f'Enqueued {added} item(s) to Playback Queue.')
        sys.exit(0)

    combined_text = '\n\n'.join(d['text'] for d in docs if d.get('text'))
    if args.speak:
        speak_sh = os.path.join(SCRIPT_DIR, 'speak.sh')
        subprocess.run(['bash', speak_sh, '--text', combined_text], env=get_sanitized_env())
    else:
        sys.stdout.write(combined_text + '\n')


if __name__ == '__main__':
    main()
