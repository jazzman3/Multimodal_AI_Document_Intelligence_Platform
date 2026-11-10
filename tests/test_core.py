import io
import json
import sqlite3
import pytest
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen.canvas import Canvas
from docintel.core import Store, DocumentError, read_document, extract_rules, validate_fields, FIELDS

INVOICE='Invoice Number: INV-100\nVendor: North Star LLC\nInvoice Date: 2026-11-10\nTotal: $124.50\nCurrency: USD'
CONTRACT='Agreement ID: C-902\nParty A: Lake Corp\nParty B: Oak Inc\nEffective Date: 2026-02-20\nGoverning Law: Texas'
FORM='Form ID: F-123\nFull Name: A Test Person\nEmail: test@example.com\nPhone: 555-0101\nSubmission Date: 2026-01-07'

def pdf(text,scan=False):
    b=io.BytesIO(); c=Canvas(b,pagesize=(612,792))
    if scan:
        image=Image.new('RGB',(1000,1300),'white'); d=ImageDraw.Draw(image)
        for i,line in enumerate(text.splitlines()):d.text((40,50+60*i),line,fill='black',font=ImageFont.truetype('DejaVuSans.ttf',28))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png') as f:
            image.save(f.name);c.drawImage(f.name,30,270,width=550,height=490)
    else:
        for i,line in enumerate(text.splitlines()):c.drawString(45,740-30*i,line)
    c.save();return b.getvalue()

def image_bytes(text,fmt='PNG'):
    img=Image.new('RGB',(1000,650),'white');d=ImageDraw.Draw(img)
    font=ImageFont.truetype('DejaVuSans.ttf',27)
    for i,line in enumerate(text.splitlines()):d.text((30,45+i*68),line,font=font,fill='black')
    b=io.BytesIO();img.save(b,format=fmt);return b.getvalue()

@pytest.mark.parametrize('kind,text', [('invoice',INVOICE),('contract',CONTRACT),('form',FORM)])
def test_rules_all_fields(kind,text):
    fields,conf,evidence=extract_rules(text,kind)
    assert all(fields.values())
    assert all(conf.values())
    assert not validate_fields(kind,fields)

def test_pdf_text():
    text,meta=read_document('invoice.pdf',pdf(INVOICE))
    assert 'INV-100' in text and meta['pages']==1 and not meta['ocr_pages']

def test_image_ocr():
    text,meta=read_document('form.png',image_bytes(FORM))
    assert 'test@example.com' in text and meta['ocr_pages']==[1]

def test_scanned_pdf_ocr():
    text,meta=read_document('invoice.pdf',pdf(INVOICE,scan=True))
    assert 'INV-100' in text and meta['ocr_pages']==[1]

def test_fake_pdf_rejected():
    with pytest.raises(DocumentError):read_document('fake.pdf',b'not a pdf')

def test_fake_image_rejected():
    with pytest.raises(DocumentError):read_document('fake.png',b'not an image')

def test_unsupported_extension_rejected():
    with pytest.raises(DocumentError):read_document('attack.svg',b'<svg />')

def test_empty_rejected():
    with pytest.raises(DocumentError):read_document('invoice.pdf',b'')

def test_oversize_rejected(monkeypatch):
    monkeypatch.setattr('docintel.core.MAX_UPLOAD_BYTES',100)
    with pytest.raises(DocumentError):read_document('invoice.pdf',pdf(INVOICE))

def test_invalid_email():
    f,_,_=extract_rules(FORM.replace('test@example.com','invalid-email'),'form')
    assert 'email: invalid' in validate_fields('form',f)

def test_date_invalid():
    f,_,_=extract_rules(INVOICE.replace('2026-11-10','2026-99-10'),'invoice')
    assert 'invoice_date: invalid calendar date' in validate_fields('invoice',f)

def test_missing_fields():
    f,_,_=extract_rules('Vendor: North Star LLC','invoice')
    assert any('invoice_number' in e for e in validate_fields('invoice',f))

def test_store_document_review_audit():
    s=Store();d=s.create('acme','invoice.pdf','invoice',pdf(INVOICE),'analyst')
    assert d['status']=='needs_review'
    assert s.verify_audit('acme')
    reviewed=s.review('acme',d['id'],'reviewer',{},True)
    assert reviewed['status']=='approved'
    assert len(s.audit('acme'))==2 and s.verify_audit('acme')

def test_reject_and_immutability():
    s=Store();d=s.create('acme','invoice.pdf','invoice',pdf(INVOICE),'analyst')
    assert s.review('acme',d['id'],'reviewer',{},False)['status']=='rejected'
    with pytest.raises(DocumentError):s.review('acme',d['id'],'reviewer',{},True)

def test_approval_invalid_blocked():
    s=Store();d=s.create('acme','invoice.pdf','invoice',pdf('Vendor: North Star LLC'),'analyst')
    with pytest.raises(DocumentError):s.review('acme',d['id'],'reviewer',{},True)

def test_tenant_scoping():
    s=Store();d=s.create('acme','invoice.pdf','invoice',pdf(INVOICE),'analyst')
    assert s.get('other',d['id']) is None and not s.list('other')
    assert s.review('other',d['id'],'reviewer',{},True) is None

def test_audit_tamper_detection():
    s=Store();d=s.create('acme','invoice.pdf','invoice',pdf(INVOICE),'analyst')
    assert s.verify_audit('acme')
    s.db.execute("UPDATE audit SET action='tampered' WHERE tenant='acme'");s.db.commit()
    assert not s.verify_audit('acme')

def test_unknown_review_field_rejected():
    s=Store();d=s.create('acme','invoice.pdf','invoice',pdf(INVOICE),'analyst')
    with pytest.raises(DocumentError):s.review('acme',d['id'],'reviewer',{'untrusted':'hello'},True)
