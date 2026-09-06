"""Pull readable text out of an uploaded document.

RFPs arrive as Word files. Until now intake took text only, so a .docx was refused at the file
picker - and refused invisibly, because the picker's `accept` list simply greyed it out.

A .docx is a zip of OOXML parts, so this needs no dependency: `zipfile` and `ElementTree` are
both stdlib. That matters here more than usual - the Dockerfile installs requirements.txt and
nothing else, and a document parser is exactly the kind of thing that drags in a tree of
transitive packages for a job the standard library already does.

DELIBERATELY NOT DONE: .doc (the pre-2007 binary format, which is a different problem entirely)
and .pdf (where text extraction is genuinely hard and the failure modes - ligatures, columns,
scanned images with no text layer at all - would produce a brief that looks fine and is subtly
wrong). Both are refused by name, so the reader is told rather than left guessing.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import List

#: WordprocessingML namespace. Every text run in a .docx lives under it.
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

#: What intake can read, and what each one is.
SUPPORTED = {
    '.txt': 'plain text',
    '.md': 'markdown',
    '.markdown': 'markdown',
    '.csv': 'comma-separated values',
    '.json': 'JSON',
    '.docx': 'Word document',
}

#: Formats a planner will plausibly try, with the reason each is refused. Named explicitly so
#: the message says why rather than just "unsupported".
KNOWN_UNSUPPORTED = {
    '.pdf': (
        'PDF text extraction is not implemented. A scanned PDF has no text layer at all, and a '
        'typeset one loses column order - either would produce a brief that looks complete and '
        'is quietly wrong.'
    ),
    '.doc': (
        'The pre-2007 .doc format is not supported. Save it as .docx from Word and upload that.'
    ),
    '.rtf': 'RTF is not supported. Save it as .docx or paste the text.',
    '.xlsx': 'Spreadsheets are not supported. Paste the relevant text, or upload it as .csv.',
    '.xls': 'Spreadsheets are not supported. Paste the relevant text, or upload it as .csv.',
    '.pages': 'Apple Pages files are not supported. Export as .docx or .txt.',
}


class UnsupportedDocument(Exception):
    """The file cannot be read, with a reason worth showing a person."""


def extension_of(filename: str) -> str:
    match = re.search(r'(\.[A-Za-z0-9]+)$', filename or '')
    return match.group(1).lower() if match else ''


def describe_support(filename: str) -> str:
    """The message for a file that cannot be read. Empty string when it can be."""
    ext = extension_of(filename)
    if ext in SUPPORTED:
        return ''
    readable = ', '.join(sorted(SUPPORTED))
    if ext in KNOWN_UNSUPPORTED:
        return f'{KNOWN_UNSUPPORTED[ext]} Readable formats: {readable}.'
    if not ext:
        return f'{filename or "That file"} has no extension, so it cannot be read. ' \
               f'Readable formats: {readable}.'
    return f'{ext} files are not supported. Readable formats: {readable}.'


def _paragraph_text(paragraph: ET.Element) -> str:
    """One paragraph, with its runs joined and its breaks respected.

    Word splits a sentence across runs whenever formatting changes mid-line, so reading run by
    run without joining would shatter "a 30 MW Tier IV facility" into fragments - and the intake
    extractor cites quotes from this text, so a shattered sentence costs a citation.
    """
    parts: List[str] = []
    for node in paragraph.iter():
        if node.tag == f'{W}t':
            parts.append(node.text or '')
        elif node.tag == f'{W}tab':
            parts.append('\t')
        elif node.tag in (f'{W}br', f'{W}cr'):
            parts.append('\n')
    return ''.join(parts)


def docx_to_text(data: bytes) -> str:
    """The readable text of a .docx, paragraph per line.

    Tables are included: a scope or equipment schedule in an RFP is usually a table, and dropping
    them would silently lose the most quotable part of the document. `iter()` walks nested
    content in document order, so a paragraph inside a table cell arrives in the right place.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocument(
            'That .docx could not be opened - it is not a valid Word file. If it was renamed '
            'from another format, save it properly as .docx from Word.'
        ) from exc

    try:
        with archive.open('word/document.xml') as handle:
            root = ET.parse(handle).getroot()
    except KeyError as exc:
        raise UnsupportedDocument(
            'That .docx has no word/document.xml, so it is not a Word document. A .zip renamed '
            'to .docx looks like this.'
        ) from exc
    except ET.ParseError as exc:
        raise UnsupportedDocument(f'That .docx is corrupt and could not be parsed: {exc}') from exc

    lines: List[str] = []
    body = root.find(f'{W}body')
    for paragraph in (body if body is not None else root).iter(f'{W}p'):
        text = _paragraph_text(paragraph).strip()
        if text:
            lines.append(text)

    if not lines:
        raise UnsupportedDocument(
            'That .docx contains no readable text. If the content is an image or a scan, there '
            'is nothing to extract - paste the brief instead.'
        )
    return '\n'.join(lines)


def document_to_text(filename: str, data: bytes) -> str:
    """Text from an uploaded document, or an UnsupportedDocument saying why not."""
    problem = describe_support(filename)
    if problem:
        raise UnsupportedDocument(problem)

    if extension_of(filename) == '.docx':
        return docx_to_text(data)

    # Everything else is already text. utf-8 first, then cp1252, which is what Word and Excel
    # produce on Windows and what a .csv exported there will be.
    for encoding in ('utf-8', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocument(
        f'{filename} is not readable as text in UTF-8 or Windows-1252. If it is a binary '
        'format, paste the brief instead.'
    )
