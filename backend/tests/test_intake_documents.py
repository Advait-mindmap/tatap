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
    pdf_to_text,
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
    assert document_to_text('x.csv', 'Chennai - 30 MW'.encode('cp1252')) == ('Chennai - 30 MW', '')


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
    response = client.post('/intake/document', files={'file': ('rfp.rtf', b'{\\rtf1}')})
    assert response.status_code == 415
    detail = response.json()['detail']
    assert 'RTF is not supported' in detail
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


# ------------------------------------------------------------------ PDFs
#
# Built here rather than committed as binaries, and built as REAL PDFs - a byte-accurate xref
# table, a content stream per page, a font resource - so these exercise pypdf's actual parsing.
# A stubbed reader would agree with whatever the extractor did and prove nothing about a file a
# planner would upload.


def make_pdf(pages):
    """A minimal but valid PDF. A page whose text is '' gets no content stream: that is what a
    scanned page looks like to an extractor, and it is the case worth testing."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    add(b'')  # 1: catalog, filled in once the page tree number is known
    pages_obj = add(b'')
    font = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')

    kids = []
    for text in pages:
        if text:
            escaped = text.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')
            stream = f'BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET'.encode('latin-1')
            content = add(
                b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream
                + b'\nendstream'
            )
            contents = b' /Contents ' + str(content).encode() + b' 0 R'
        else:
            contents = b''
        kids.append(add(
            b'<< /Type /Page /Parent ' + str(pages_obj).encode() + b' 0 R '
            b'/MediaBox [0 0 612 792]' + contents +
            b' /Resources << /Font << /F1 ' + str(font).encode() + b' 0 R >> >> >>'
        ))

    objects[0] = b'<< /Type /Catalog /Pages ' + str(pages_obj).encode() + b' 0 R >>'
    objects[pages_obj - 1] = (
        b'<< /Type /Pages /Count ' + str(len(kids)).encode() + b' /Kids ['
        + b' '.join(str(k).encode() + b' 0 R' for k in kids) + b'] >>'
    )

    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b' 0 obj\n' + body + b'\nendobj\n'

    start = len(out)
    out += b'xref\n0 ' + str(len(objects) + 1).encode() + b'\n0000000000 65535 f \n'
    for offset in offsets:
        out += f'{offset:010d} 00000 n \n'.encode()
    out += (b'trailer\n<< /Size ' + str(len(objects) + 1).encode()
            + b' /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n')
    return bytes(out)


def test_the_fixture_really_is_a_pdf_a_parser_accepts():
    """If make_pdf produced something only our own code tolerated, every test below would be
    testing the fixture rather than the extractor."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(make_pdf(['One', 'Two'])))
    assert len(reader.pages) == 2


def test_a_pdf_brief_is_read():
    text, notice = pdf_to_text(make_pdf([
        'We are bidding a 30 MW Tier IV data centre in Chennai.',
        'Topology is 2N on both trains and the scope is design-build.',
    ]))
    assert '30 MW Tier IV' in text
    assert 'design-build' in text
    assert notice == '', f'a fully readable PDF should raise no notice, got: {notice}'


def test_pages_arrive_in_order():
    """Out-of-order pages would produce a brief that reads plausibly and says the wrong thing."""
    text, _ = pdf_to_text(make_pdf(['Alpha first', 'Bravo second', 'Charlie third']))
    assert text.index('Alpha') < text.index('Bravo') < text.index('Charlie')


def test_a_scanned_pdf_is_refused_rather_than_returned_empty():
    """The failure the module was written to avoid: a file that looks like it worked.

    A scan has no text layer, so extraction succeeds and yields nothing. Returning that would
    put an empty brief in the box under a filename the reader trusts.
    """
    with pytest.raises(UnsupportedDocument, match='no text layer'):
        pdf_to_text(make_pdf(['', '', '']))


def test_a_pdf_with_a_scrap_of_text_is_still_refused():
    """A stray caption on a scanned page is not a brief. The threshold exists so a handful of
    characters is refused like the scan it is rather than accepted as content."""
    with pytest.raises(UnsupportedDocument, match='no text layer'):
        pdf_to_text(make_pdf(['Fig 1', '', '']))


def test_a_partly_scanned_pdf_returns_its_text_and_says_what_was_missed():
    """The dangerous middle case, and the reason the notice exists at all.

    Half an RFP that reads correctly is exactly the outcome nobody double-checks. The text comes
    back, and the notice names how many pages did not.
    """
    text, notice = pdf_to_text(make_pdf([
        'We are bidding a 30 MW Tier IV data centre in Chennai.', '',
        'Delivery is design-build with turnkey electrical.', '',
    ]))
    assert '30 MW' in text and 'design-build' in text
    assert notice, 'a partly-readable PDF raised no notice'
    assert '2 of 4 pages' in notice
    assert 'page 2, 4' in notice


def test_a_corrupt_pdf_says_so():
    with pytest.raises(UnsupportedDocument, match='could not be opened'):
        pdf_to_text(b'%PDF-1.4 and then nothing that parses')


def test_a_password_protected_pdf_says_so():
    import pypdf

    writer = pypdf.PdfWriter(clone_from=io.BytesIO(make_pdf(['Confidential brief text here'])))
    writer.encrypt('a-real-password')
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(UnsupportedDocument, match='password-protected'):
        pdf_to_text(buffer.getvalue())


def test_pdf_is_offered_as_a_readable_format():
    assert '.pdf' in SUPPORTED
    assert describe_support('rfp.pdf') == '', 'PDF is supported but still described as refused'


def test_the_endpoint_reads_a_pdf_and_passes_the_notice_on(client):
    data = make_pdf(['We are bidding a 30 MW Tier IV data centre in Chennai.', ''])
    response = client.post('/intake/document', files={'file': ('rfp.pdf', data)})
    assert response.status_code == 200
    body = response.json()
    assert '30 MW Tier IV' in body['text']
    assert '1 of 2 pages' in body['notice']


def test_the_endpoint_refuses_a_scan_with_a_reason(client):
    response = client.post('/intake/document', files={'file': ('scan.pdf', make_pdf(['', '']))})
    assert response.status_code == 415
    assert 'no text layer' in response.json()['detail']


def test_a_compressed_pdf_extracts_correctly():
    """Every PDF a planner will actually upload is compressed.

    Word, Acrobat and every other producer FlateDecode their content streams; the hand-built
    fixtures above do not. Without this, the whole PDF suite would be exercising a path no real
    file takes. The compression is applied by pypdf itself rather than by hand, so the fixture
    cannot be compressed in some way only our own reader would accept.
    """
    import pypdf

    writer = pypdf.PdfWriter(clone_from=io.BytesIO(make_pdf([
        'We are bidding a 30 MW Tier IV data centre in Chennai.',
        'Topology is 2N and the scope is design-build.',
    ])))
    for page in writer.pages:
        page.compress_content_streams()
    buffer = io.BytesIO()
    writer.write(buffer)

    reader = pypdf.PdfReader(io.BytesIO(buffer.getvalue()))
    assert all(
        page['/Contents'].get_object().get('/Filter') == '/FlateDecode' for page in reader.pages
    ), 'the fixture is not actually compressed, so this proves nothing'

    text, notice = pdf_to_text(buffer.getvalue())
    assert '30 MW Tier IV' in text
    assert 'design-build' in text
    assert notice == ''
