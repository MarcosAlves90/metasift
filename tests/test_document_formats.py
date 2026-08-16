from __future__ import annotations

import io
import json
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter

from metasift.adapters import json_document, markdown, ooxml, pdf
from metasift.engine import clean_file, inspect_file
from metasift.models import CleanMode
from metasift.resource_limits import ResourceBudget


def make_ooxml() -> bytes:
    out=io.BytesIO()
    core=b'''<?xml version="1.0" encoding="UTF-8"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Alice</dc:creator><dc:title>Report</dc:title><dc:description>Generated with ChatGPT</dc:description></cp:coreProperties>'''
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',b'<Types/>'); z.writestr('docProps/core.xml',core); z.writestr('word/document.xml',b'<document>Hello</document>')
    return out.getvalue()


def make_pdf(*, encrypted: bool=False) -> bytes:
    writer=PdfWriter(); writer.add_blank_page(width=200,height=300)
    writer.add_metadata({'/Author':'Alice','/Title':'Keep title','/Creator':'ChatGPT'})
    writer.xmp_metadata=b'''<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:RDF><rdf:Description><prompt>OpenAI workflow</prompt><project>keep me</project></rdf:Description></rdf:RDF></x:xmpmeta>'''
    if encrypted: writer.encrypt('secret')
    out=io.BytesIO(); writer.write(out); return out.getvalue()


def test_json_field_level_and_semantic_preservation(tmp_path):
    data=json.dumps({'name':'payload','metadata':{'author':'Alice','prompt':'OpenAI workflow','project':'keep'},'items':[1,2,3]},indent=4).encode()+b'\n'
    entries=json_document.inspect(data)
    assert {e.key for e in entries}=={'author','prompt','project'}
    cleaned,removed,kept=json_document.clean(data,CleanMode.SHARE_SAFE)
    assert {e.key for e in removed}=={'author','prompt'}
    parsed=json.loads(cleaned)
    assert parsed['name']=='payload' and parsed['items']==[1,2,3]
    assert parsed['metadata']=={'project':'keep'}
    maximal,removed,_=json_document.clean(data,CleanMode.METADATA_MAX)
    assert 'metadata' not in json.loads(maximal)
    path=tmp_path/'data.json'; path.write_bytes(data)
    result=clean_file(path,mode=CleanMode.SHARE_SAFE)
    assert result.adapter=='json'
    assert inspect_file(result.destination).adapter=='json'


def test_json_invalid_and_bom_round_trip():
    with pytest.raises(ValueError,match='invalid UTF-8 JSON'):
        json_document.inspect(b'{broken')
    data=b'\xef\xbb\xbf{"_meta":{"author":"Alice"},"value":1}\n'
    cleaned,removed,_=json_document.clean(data,CleanMode.PRIVACY)
    assert cleaned.startswith(b'\xef\xbb\xbf') and cleaned.endswith(b'\n')
    assert {e.key for e in removed}=={'author'} and json.loads(cleaned.decode('utf-8-sig'))=={'value':1}


def test_text_document_adapters_enforce_direct_file_budget():
    budget = ResourceBudget(max_file_bytes=4)
    with pytest.raises(ValueError, match='safety limit'):
        json_document.inspect(b'{"metadata":{}}', budget=budget)
    with pytest.raises(ValueError, match='safety limit'):
        markdown.inspect(b'---\na: b\n---\n', budget=budget)


def test_markdown_yaml_frontmatter_preserves_body(tmp_path):
    body='# Heading\n\nBody --- stays exactly.\n'
    data=('---\nauthor: Alice\nprompt: OpenAI workflow\ntitle: Keep\ntags:\n  - one\n  - two\n---\n'+body).encode()
    assert {e.key for e in markdown.inspect(data)}=={'author','prompt','title','tags'}
    cleaned,removed,_=markdown.clean(data,CleanMode.SHARE_SAFE)
    assert {e.key for e in removed}=={'author','prompt'}
    text=cleaned.decode(); assert 'title: Keep' in text and 'tags:' in text and text.endswith(body)
    maximal,removed,_=markdown.clean(data,CleanMode.METADATA_MAX)
    assert maximal.decode()==body
    path=tmp_path/'note.md'; path.write_bytes(data)
    result=clean_file(path,mode=CleanMode.METADATA_MAX)
    assert result.adapter=='markdown' and result.destination.read_text()==body


def test_markdown_toml_custom_and_unterminated():
    data=b'+++\nauthor = "Alice"\ntitle = "Keep"\n+++\nBody\n'
    cleaned,removed,_=markdown.clean(data,CleanMode.CUSTOM,remove_keys=('markdown.frontmatter.author',))
    assert {e.key for e in removed}=={'author'} and b'title = "Keep"' in cleaned
    with pytest.raises(ValueError,match='unterminated'):
        markdown.inspect(b'---\nauthor: Alice\nBody')
    plain=b'# no metadata\n'; assert markdown.inspect(plain)==(); assert markdown.clean(plain,CleanMode.FULL)[0]==plain


@pytest.mark.parametrize('suffix',[
    '.docx','.docm','.dotx','.dotm',
    '.xlsx','.xlsm','.xlsb','.xltx','.xltm','.xlam',
    '.pptx','.pptm','.potx','.potm','.ppsx','.ppsm','.ppam',
])
def test_ooxml_modern_variants_are_selected_and_cleaned(tmp_path,suffix):
    data=make_ooxml(); assert ooxml.matches(data,suffix)
    path=tmp_path/f'sample{suffix}'; path.write_bytes(data)
    report=inspect_file(path); assert report.adapter=='ooxml'
    result=clean_file(path,mode=CleanMode.PRIVACY)
    assert {e.key for e in result.removed}=={'creator'}
    with zipfile.ZipFile(result.destination) as z: assert b'Hello' in z.read('word/document.xml')


def test_pdf_info_xmp_and_page_preservation(tmp_path):
    data=make_pdf(); entries=pdf.inspect(data); keys={e.key for e in entries}
    assert {'Author','Title','Creator','prompt','project'} <= keys
    cleaned,removed,kept=pdf.clean(data,CleanMode.SHARE_SAFE)
    assert {'Author','Creator','prompt'} <= {e.key for e in removed}
    remaining={e.key for e in pdf.inspect(cleaned)}
    assert 'Author' not in remaining and 'Creator' not in remaining and 'prompt' not in remaining
    assert {'Title','project'} <= remaining
    before=PdfReader(io.BytesIO(data)); after=PdfReader(io.BytesIO(cleaned))
    assert len(before.pages)==len(after.pages)==1
    assert tuple(before.pages[0].mediabox)==tuple(after.pages[0].mediabox)
    maximal,removed,_=pdf.clean(data,CleanMode.METADATA_MAX)
    assert pdf.inspect(maximal)==()
    path=tmp_path/'document.pdf'; path.write_bytes(data)
    result=clean_file(path,mode=CleanMode.SHARE_SAFE)
    assert result.adapter=='pdf' and inspect_file(result.destination).adapter=='pdf'


def test_pdf_encrypted_fails_closed():
    encrypted = make_pdf(encrypted=True)
    with pytest.raises(ValueError, match='encrypted PDF'):
        pdf.inspect(encrypted)

def make_signed_pdf() -> bytes:
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject
    writer=PdfWriter(); writer.add_blank_page(width=100,height=100); writer.add_metadata({'/Author':'Alice'})
    field=DictionaryObject({NameObject('/FT'):NameObject('/Sig'),NameObject('/T'):TextStringObject('Signature1')})
    field_ref=writer._add_object(field)
    acro=DictionaryObject({NameObject('/Fields'):ArrayObject([field_ref])})
    writer.root_object[NameObject('/AcroForm')]=writer._add_object(acro)
    out=io.BytesIO(); writer.write(out); return out.getvalue()


def test_pdf_signed_inventory_and_rewrite_refusal():
    data=make_signed_pdf(); entries=pdf.inspect(data)
    signature=next(e for e in entries if e.key=='DigitalSignature')
    assert not signature.removable and signature.provenance_related and signature.removal_impact=='signature-invalidation'
    with pytest.raises(ValueError,match='digitally signed PDF'):
        pdf.clean(data,CleanMode.METADATA_MAX)


def test_pdf_invalid_custom_and_noop_paths():
    with pytest.raises(ValueError,match='invalid PDF'):
        pdf.inspect(b'%PDF-broken')
    data=make_pdf()
    cleaned,removed,kept=pdf.clean(data,CleanMode.CUSTOM,remove_keys=('pdf.info.Title',))
    assert {e.key for e in removed}=={'Title'}
    assert 'Title' not in {e.key for e in pdf.inspect(cleaned)}
    untouched,removed,kept=pdf.clean(data,CleanMode.CUSTOM,remove_keys=('does.not.exist',))
    assert untouched==data and removed==() and kept
    assert pdf.matches(b'  %PDF-1.7\n') and pdf.matches(b'x','.PDF')


def test_json_non_object_and_markdown_unparsed_full_removal():
    data=b'[1,2,3]\n'; assert json_document.inspect(data)==(); assert json_document.clean(data,CleanMode.FULL)[0]==data
    md=b'---\n[odd]\nauthor: Alice\n---\nBody\n'
    entries=markdown.inspect(md); assert 'FrontMatter' in {e.key for e in entries}
    cleaned,removed,_=markdown.clean(md,CleanMode.METADATA_MAX)
    assert cleaned==b'Body\n' and {e.key for e in removed}>={'FrontMatter','author'}


def test_capability_matrix_contains_document_adapters():
    from metasift.capabilities import capability_matrix, capabilities_for
    matrix=capability_matrix()
    assert {'json','markdown','pdf','ooxml'} <= matrix.keys()
    assert capabilities_for('missing')['sanitize'] is False

def test_cli_e2e_document_formats(tmp_path, capsys):
    from metasift.cli import main

    json_path=tmp_path/'payload.json'
    json_path.write_text(json.dumps({'payload':1,'metadata':{'author':'Alice','prompt':'OpenAI','keep':'x'}}),encoding='utf-8')
    assert main(['sanitize',str(json_path),'--mode','share-safe','--json'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['adapter']=='json'
    assert json.loads((tmp_path/'payload.cleaned.json').read_text())=={'payload':1,'metadata':{'keep':'x'}}

    md_path=tmp_path/'note.md'; md_path.write_text('---\nauthor: Alice\ntitle: Keep\n---\nBody\n',encoding='utf-8')
    assert main(['sanitize',str(md_path),'--mode','privacy','--json'])==0
    result=json.loads(capsys.readouterr().out); assert result['adapter']=='markdown'
    assert (tmp_path/'note.cleaned.md').read_text()=='---\ntitle: Keep\n---\nBody\n'

    pdf_path=tmp_path/'doc.pdf'; pdf_path.write_bytes(make_pdf())
    assert main(['sanitize',str(pdf_path),'--mode','share-safe','--json'])==0
    result=json.loads(capsys.readouterr().out); assert result['adapter']=='pdf'
    remaining={e.key for e in pdf.inspect((tmp_path/'doc.cleaned.pdf').read_bytes())}
    assert 'Author' not in remaining and 'Creator' not in remaining and 'prompt' not in remaining
