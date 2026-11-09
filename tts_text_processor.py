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
import json
import os
import re
import sys
import time
import unicodedata

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
    max_chars: int = 20000,
) -> str:
    """Clean control characters, markdown formatting, URLs, and technical symbols."""
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
        # Strip standalone HTML tags (only actual HTML tags, not < comparison)
        text = re.sub(r'</?[a-zA-Z][a-zA-Z0-9_-]*(?:\s+[^>]*)?>', ' ', text)
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
    if source_lang and source_lang != 'auto':
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
) -> int:
    os.makedirs(workdir, exist_ok=True)

    cleaned_text = clean_and_filter_text(
        raw_text,
        filter_markdown=filter_markdown,
        filter_urls=filter_urls,
        filter_code=filter_code,
        replace_symbols=replace_symbols,
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
    for idx, (lang, chunk_text) in enumerate(chunks):
        pad = f'{idx:03d}'
        with open(os.path.join(workdir, f'seg_{pad}.txt'), 'w', encoding='utf-8') as f:
            f.write(chunk_text)
        with open(os.path.join(workdir, f'seg_{pad}.lang'), 'w', encoding='utf-8') as f:
            f.write(lang)
        with open(os.path.join(workdir, f'seg_{pad}.status'), 'w', encoding='utf-8') as f:
            f.write('pending')

        est_dur = round(max(1.2, len(chunk_text) / 14.5), 2)
        manifest_chunks.append({
            'id': idx,
            'pad': pad,
            'lang': lang,
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
    parser.add_argument('--workdir', required=True, help='Work directory for segment files')
    parser.add_argument('--source-lang', default='auto', help='Source language code or auto')
    parser.add_argument('--pause-punctuation', default='no', help='yes/no')
    parser.add_argument('--first-chunk-max', type=int, default=220)
    parser.add_argument('--max-segment-chars', type=int, default=480)
    parser.add_argument('--filter-markdown', default='yes')
    parser.add_argument('--filter-urls', default='yes')
    parser.add_argument('--filter-code', default='no')
    parser.add_argument('--replace-symbols', default='yes')
    args = parser.parse_args()

    raw_text = sys.stdin.read()
    count = process_and_write_segments(
        raw_text=raw_text,
        workdir=args.workdir,
        source_lang=args.source_lang,
        pause_punctuation=to_bool(args.pause_punctuation, False),
        first_chunk_max=args.first_chunk_max,
        max_segment_chars=args.max_segment_chars,
        filter_markdown=to_bool(args.filter_markdown, True),
        filter_urls=to_bool(args.filter_urls, True),
        filter_code=to_bool(args.filter_code, False),
        replace_symbols=to_bool(args.replace_symbols, True),
    )
    print(count)


if __name__ == '__main__':
    main()
