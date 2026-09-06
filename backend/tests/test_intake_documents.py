"""Reading an uploaded document, and refusing one out loud.

Found by direct testing: the file picker's accept list omitted .docx, so a Word file could not
be selected at all - and a .docx fed to the input programmatically produced no reaction the
reader could see. RFPs arrive as Word documents, so "not supported" was the wrong answer; and
"not supported, silently" was the wrong answer for every other format too.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.app.intake.documents import (
    SUPPORTED,
    UnsupportedDocument,
    describe_support,
    docx_to_text,
    document_to_text,
)
from backend.app.main import app

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def make_docx(paragraphs, *, omit_document=False, extra_body=''):
    """A real .docx: a zip of OOXML parts, the way Word writes one."""
    body = ''.join(
        f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs
    ) + extra_body
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('_rels/.rels', '<Relationships/>')
        if not omit_document:
            archive.writestr('word/document.xml', document)
    return buffer.getvalue()


@pytest.fixture()
def client():
    return TestClient(app)


# ------------------------------------------------------------------ reading a Word file


def test_a_docx_yields_its_paragraphs():
    text = docx_to_text(make_docx([
        'Request for Proposal',
        'We are bidding a 30 MW Tier IV data centre in Chennai.',
        '2N electrical topology.',
    ]))
    assert text.splitlines() == [
        'Request for Proposal',
        'We are bidding a 30 MW Tier IV data centre in Chennai.',
        '2N electrical topology.',
    ]


def test_a_sentence_split_across_runs_comes_back_whole():
    """Word starts a new run wherever formatting changes, so one sentence is routinely several
    runs. Reading run by run would shatter "30 MW Tier IV" into fragments - and intake cites
    quotes from this text, so a shattered sentence costs a citation.
    """
    split = (
        '<w:p>'
        '<w:r><w:t xml:space="preserve">We are bidding a </w:t></w:r>'
        '<w:r><w:t xml:space="preserve">30 MW</w:t></w:r>'
        '<w:r><w:t xml:space="preserve"> Tier IV data centre.</w:t></w:r>'
        '</w:p>'
    )
    text = docx_to_text(make_docx([], extra_body=split))
    assert text == 'We are bidding a 30 MW Tier IV data centre.'
    assert '30 MW' in text, 'the quote intake would cite is not intact'


def test_table_content_is_not_dropped():
    """A scope or equipment schedule in an RFP is usually a table; dropping tables loses the
    most quotable part of the document.
    """
    table = (
        '<w:tbl><w:tr><w:tc>'
        '<w:p><w:r><w:t>Transformers</w:t></w:r></w:p>'
        '</w:tc><w:tc>'
        '<w:p><w:r><w:t>Owner-furnished</w:t></w:r></w:p>'
        '</w:tc></w:tr></w:tbl>'
    )
    text = docx_to_text(make_docx(['Scope'], extra_body=table))
    assert 'Transformers' in text
    assert 'Owner-furnished' in text


def test_line_breaks_inside_a_paragraph_survive():
    para = '<w:p><w:r><w:t>Line one</w:t><w:br/><w:t>Line two</w:t></w:r></w:p>'
    assert docx_to_text(make_docx([], extra_body=para)) == 'Line one\nLine two'


# ------------------------------------------------------------------ refusing, with a reason


def test_a_renamed_zip_is_refused_by_name():
    with pytest.raises(UnsupportedDocument, match='not a valid Word file'):
        docx_to_text(b'this is not a zip')


def test_a_zip_without_a_document_part_is_refused():
    with pytest.raises(UnsupportedDocument, match='no word/document.xml'):
        docx_to_text(make_docx(['x'], omit_document=True))


def test_a_docx_with_no_text_says_so_rather_than_returning_nothing():
    """A scanned page in a Word wrapper has no text layer. Returning an empty string would look
    like a successful read of an empty brief.
    """
    with pytest.raises(UnsupportedDocument, match='no readable text'):
        docx_to_text(make_docx([]))


@pytest.mark.parametrize('name,expected', [
    ('brief.pdf', 'PDF text extraction is not implemented'),
    ('brief.doc', 'pre-2007 .doc format'),
    ('brief.xlsx', 'Spreadsheets are not supported'),
    ('brief.rtf', 'RTF is not supported'),
])
def test_common_formats_are_refused_by_name_not_generically(name, expected):
    """A bare "unsupported file type" tells a person nothing. Each of these says what it is and
    what to do instead.
    """
    message = describe_support(name)
    assert expected in message
    assert '.docx' in message, 'the message does not say what IS readable'


def test_an_unknown_extension_still_lists_what_is_readable():
    message = describe_support('brief.zzz')
    assert '.zzz files are not supported' in message
    for extension in SUPPORTED:
        assert extension in message


def test_a_supported_file_has_no_complaint():
    for name in ('a.txt', 'b.md', 'c.markdown', 'd.csv', 'e.json', 'f.docx', 'UPPER.DOCX'):
        assert describe_support(name) == '', name


# ------------------------------------------------------------------ text files


def test_a_windows_encoded_csv_is_read():
    """Word and Excel on Windows write cp1252, and a CSV exported there is the likeliest
    non-UTF-8 file a planner will upload.
    """
    assert document_to_text('x.csv', 'Chennai - 30 MW'.encode('cp1252')) == 'Chennai - 30 MW'


def test_binary_dressed_as_text_is_refused_rather_than_mangled():
    with pytest.raises(UnsupportedDocument, match='not readable as text'):
        document_to_text('x.txt', bytes(range(256)) * 4)


# ------------------------------------------------------------------ the endpoint


def test_the_endpoint_returns_the_text_not_a_brief(client):
    """Deliberately the text: the reader sees what came out of their Word file and can correct
    it before anything is extracted from it.
    """
    data = make_docx(['We are bidding a 30 MW Tier IV data centre in Chennai.'])
    response = client.post('/intake/document', files={'file': ('rfp.docx', data)})
    assert response.status_code == 200
    body = response.json()
    assert body['filename'] == 'rfp.docx'
    assert '30 MW Tier IV' in body['text']
    assert body['characters'] == len(body['text'])
    assert 'brief' not in body, 'the endpoint should read, not extract'


def test_an_unsupported_upload_is_415_with_a_reason(client):
    response = client.post('/intake/document', files={'file': ('rfp.pdf', b'%PDF-1.4')})
    assert response.status_code == 415
    detail = response.json()['detail']
    assert 'PDF text extraction is not implemented' in detail
    assert '.docx' in detail, 'the refusal does not say what IS readable'


def test_an_empty_upload_is_refused(client):
    response = client.post('/intake/document', files={'file': ('empty.txt', b'')})
    assert response.status_code == 400
    assert 'empty' in response.json()['detail']


def test_an_oversized_upload_is_refused(client):
    from backend.app.main import MAX_UPLOAD_BYTES

    response = client.post(
        '/intake/document',
        files={'file': ('huge.txt', b'x' * (MAX_UPLOAD_BYTES + 10))},
    )
    assert response.status_code == 413
    assert 'larger than' in response.json()['detail']


def test_the_formats_endpoint_matches_what_is_actually_readable(client):
    """The UI reads this rather than keeping its own list, so the two cannot drift."""
    body = client.get('/intake/formats').json()
    assert body['supported'] == sorted(SUPPORTED)
    assert '.docx' in body['supported']
