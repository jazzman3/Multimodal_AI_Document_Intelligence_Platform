import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageOps, UnidentifiedImageError
import pytesseract

MAX_UPLOAD_BYTES = int(os.getenv('MAX_UPLOAD_BYTES', str(8 * 1024 * 1024)))
MAX_PAGES = int(os.getenv('MAX_PAGES', '12'))
Image.MAX_IMAGE_PIXELS = 18_000_000
EXTENSIONS = {'.pdf': 'pdf', '.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp'}
FIELDS = {
    'invoice': ['invoice_number', 'vendor', 'invoice_date', 'total', 'currency'],
    'contract': ['agreement_id', 'party_a', 'party_b', 'effective_date', 'governing_law'],
    'form': ['form_id', 'full_name', 'email', 'phone', 'submission_date'],
}
PATTERNS = {
    'invoice': {
        'invoice_number': r'(?:invoice\s*(?:number|no\.?|#))\s*[:#-]?\s*([^\n]+)',
        'vendor': r'(?:vendor|supplier)\s*[:#-]?\s*([^\n]+)',
        'invoice_date': r'(?:invoice\s*date|date\s*issued)\s*[:#-]?\s*([^\n]+)',
        'total': r'(?:grand\s*total|total\s*due|total)\s*[:$ ]*([\d,]+(?:\.\d{2})?)',
        'currency': r'(?:currency)\s*[:#-]?\s*([^\n]+)',
    },
    'contract': {
        'agreement_id': r'(?:agreement\s*(?:id|number)|contract\s*(?:id|number))\s*[:#-]?\s*([^\n]+)',
        'party_a': r'(?:party\s*a|first\s*party)\s*[:#-]?\s*([^\n]+)',
        'party_b': r'(?:party\s*b|second\s*party)\s*[:#-]?\s*([^\n]+)',
        'effective_date': r'(?:effective\s*date)\s*[:#-]?\s*([^\n]+)',
        'governing_law': r'(?:governing\s*law|jurisdiction)\s*[:#-]?\s*([^\n]+)',
    },
    'form': {
        'form_id': r'(?:form\s*(?:id|number))\s*[:#-]?\s*([^\n]+)',
        'full_name': r'(?:full\s*name|applicant\s*name)\s*[:#-]?\s*([^\n]+)',
        'email': r'(?:e-?mail)\s*[:#-]?\s*([^\n]+)',
        'phone': r'(?:phone(?:\s*number)?)\s*[:#-]?\s*([^\n]+)',
        'submission_date': r'(?:submission\s*date|submitted\s*on)\s*[:#-]?\s*([^\n]+)',
    },
}

def utc():
    return datetime.now(timezone.utc).isoformat()

class DocumentError(ValueError):
    pass

def _pdf_text(data: bytes):
    try:
        pdf = fitz.open(stream=data, filetype='pdf')
    except Exception as exc:
        raise DocumentError('Invalid PDF') from exc
    try:
        if pdf.needs_pass:
            raise DocumentError('Encrypted PDF is not supported')
        if len(pdf) > MAX_PAGES:
            raise DocumentError('PDF page limit exceeded')
        chunks = []
        scan_pages = []
        for page in pdf:
            txt = page.get_text('text')
            if len(txt.strip()) < 24:
                pix = page.get_pixmap(dpi=150, alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes('png')))
                txt = pytesseract.image_to_string(img, config='--psm 6')
                scan_pages.append(page.number+1)
            chunks.append(txt)
        return '\n'.join(chunks), {'pages': len(pdf), 'ocr_pages': scan_pages}
    finally:
        pdf.close()

def _image_text(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('PNG','JPEG','WEBP'):
                raise DocumentError('Unsupported image content')
            im.load()
            if im.width * im.height > 18_000_000:
                raise DocumentError('Image pixel limit exceeded')
            img = ImageOps.exif_transpose(im).convert('RGB')
            txt = pytesseract.image_to_string(img, config='--psm 6')
            return txt, {'pages': 1, 'ocr_pages': [1], 'image_size': [img.width, img.height]}
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentError('Invalid image') from exc

def read_document(filename: str, data: bytes):
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError('Empty or oversized file')
    suffix = Path(filename).suffix.lower()
    typ = EXTENSIONS.get(suffix)
    if typ is None:
        raise DocumentError('Only PDF, PNG, JPEG and WEBP are accepted')
    if typ == 'pdf':
        if not data.startswith(b'%PDF-'):
            raise DocumentError('Content does not match PDF extension')
        return _pdf_text(data)
    if not (data.startswith(b'\x89PNG\r\n\x1a\n') if typ == 'png' else
            data.startswith(b'\xff\xd8\xff') if typ == 'jpeg' else
            data.startswith(b'RIFF') and data[8:12] == b'WEBP'):
        raise DocumentError('Content does not match image extension')
    return _image_text(data)

def extract_rules(text: str, kind: str):
    if kind not in FIELDS:
        raise DocumentError('Unknown schema')
    fields, confidence, evidence = {}, {}, {}
    for name in FIELDS[kind]:
        m = re.search(PATTERNS[kind][name], text, flags=re.IGNORECASE)
        value = m.group(1).strip().rstrip(' .') if m else None
        if value is not None and name == 'total':
            try:
                value = f'{float(value.replace(",", "")):.2f}'
            except ValueError:
                value = None
        fields[name] = value
        confidence[name] = 0.86 if value else 0.0
        evidence[name] = text[max(0, m.start()-10): min(len(text),m.end()+10)].strip() if m else None
    return fields, confidence, evidence

def validate_fields(kind: str, fields: dict):
    errors = []
    for k in FIELDS[kind]:
        if not fields.get(k):
            errors.append(f'{k}: missing')
    for datekey in ('invoice_date','effective_date','submission_date'):
        if fields.get(datekey) and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(fields[datekey])):
            errors.append(f'{datekey}: expected YYYY-MM-DD')
        elif fields.get(datekey):
            try:
                datetime.strptime(fields[datekey], '%Y-%m-%d')
            except ValueError:
                errors.append(f'{datekey}: invalid calendar date')
    if kind == 'invoice' and fields.get('total'):
        try:
            if float(fields['total']) < 0:
                errors.append('total: negative')
        except (ValueError, TypeError):
            errors.append('total: not numeric')
    if kind == 'form' and fields.get('email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', str(fields['email'])):
        errors.append('email: invalid')
    return errors

def extract_vision(data: bytes, filename: str, kind: str):
    """Optional paid external provider. No fallback from provider failures to invented values."""
    if not os.getenv('OPENAI_API_KEY'):
        raise DocumentError('Vision provider requires OPENAI_API_KEY')
    from openai import OpenAI
    suffix = Path(filename).suffix.lower()
    mime = 'application/pdf' if suffix == '.pdf' else ('image/jpeg' if suffix in ('.jpg', '.jpeg') else ('image/webp' if suffix == '.webp' else 'image/png'))
    encoded = base64.b64encode(data).decode('ascii')
    input_part = {'type':'input_file','filename':filename,'file_data':f'data:{mime};base64,{encoded}'} if mime=='application/pdf' else {'type':'input_image','image_url':f'data:{mime};base64,{encoded}'}
    schema = FIELDS[kind]
    client = OpenAI(timeout=45.0, max_retries=1)
    response = client.responses.create(
        model=os.getenv('VISION_MODEL','gpt-4.1-mini'),
        input=[{'role':'user','content':[
            {'type':'input_text','text':f'Extract ONLY these {kind} fields into one JSON object, using null for absent fields: {", ".join(schema)}. Do not infer absent values. Every non-null value must appear in the document. Reply JSON only.'},
            input_part
        ]}], max_output_tokens=500
    )
    raw = response.output_text.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed,dict):
            raise ValueError('invalid format')
        fields = {name: (str(parsed[name]).strip() if parsed.get(name) is not None else None) for name in schema}
    except (ValueError, KeyError, TypeError) as exc:
        raise DocumentError('Vision provider returned invalid JSON') from exc
    return fields, {k: None for k in schema}, {k:'vision-model-proposal; verify against document' for k in schema}

class Store:
    def __init__(self, path=':memory:'):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS documents(
          id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT NOT NULL, filename TEXT NOT NULL,
          digest TEXT NOT NULL, status TEXT NOT NULL, provider TEXT NOT NULL,
          fields TEXT NOT NULL, confidence TEXT NOT NULL, evidence TEXT NOT NULL,
          validation TEXT NOT NULL, meta TEXT NOT NULL, reviewer TEXT, created TEXT NOT NULL, updated TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_docs_tenant ON documents(tenant,created);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, doc_id TEXT NOT NULL,
          action TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL,
          prev_hash TEXT NOT NULL, hash TEXT NOT NULL
        );
        ''')
        self.db.commit()
    def _audit(self, tenant, doc_id, action, actor, detail):
        prev = self.db.execute('SELECT hash FROM audit WHERE tenant=? ORDER BY id DESC LIMIT 1',(tenant,)).fetchone()
        prev_hash = prev['hash'] if prev else 'GENESIS'
        at = utc()
        payload = json.dumps([tenant,doc_id,action,actor,detail,at,prev_hash],separators=(',',':'),sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.db.execute('INSERT INTO audit(tenant,doc_id,action,actor,detail,at,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?)',
            (tenant,doc_id,action,actor,json.dumps(detail,sort_keys=True),at,prev_hash,digest))
    def create(self, tenant, filename, kind, data, actor, provider='rules'):
        text,meta=read_document(filename,data)
        if provider=='rules':
            fields,confidence,evidence=extract_rules(text,kind)
        elif provider=='vision':
            fields,confidence,evidence=extract_vision(data,filename,kind)
        else:
            raise DocumentError('Unsupported provider')
        errors=validate_fields(kind,fields)
        now=utc(); docid=str(uuid.uuid4()); status='needs_review'
        with self.lock:
            self.db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (docid,tenant,kind,Path(filename).name[:120],hashlib.sha256(data).hexdigest(),status,provider,
                json.dumps(fields),json.dumps(confidence),json.dumps(evidence),json.dumps(errors),json.dumps(meta),None,now,now))
            self._audit(tenant,docid,'ingested',actor,{'kind':kind,'provider':provider,'errors':len(errors)})
            self.db.commit()
        return self.get(tenant,docid)
    def get(self,tenant,docid):
        with self.lock:
            row=self.db.execute('SELECT * FROM documents WHERE tenant=? AND id=?',(tenant,docid)).fetchone()
        if not row: return None
        d=dict(row)
        for k in ('fields','confidence','evidence','validation','meta'):
            d[k]=json.loads(d[k])
        return d
    def list(self,tenant):
        with self.lock:
            ids=[r['id'] for r in self.db.execute('SELECT id FROM documents WHERE tenant=? ORDER BY created DESC LIMIT 100',(tenant,)).fetchall()]
        return [self.get(tenant,id) for id in ids]
    def review(self,tenant,docid,actor,fields,approve):
        with self.lock:
            doc=self.get(tenant,docid)
            if doc is None: return None
            if doc['status']!='needs_review':
                raise DocumentError('Document has already been reviewed')
            if set(fields)-set(FIELDS[doc['kind']]):
                raise DocumentError('Unexpected fields')
            merged={**doc['fields'],**fields}
            errors=validate_fields(doc['kind'],merged)
            if approve and errors:
                raise DocumentError('Cannot approve invalid fields: '+', '.join(errors))
            status='approved' if approve else 'rejected'
            now=utc()
            self.db.execute('UPDATE documents SET status=?, fields=?,validation=?,reviewer=?,updated=? WHERE id=? AND tenant=?',
                (status,json.dumps(merged),json.dumps(errors),actor,now,docid,tenant))
            self._audit(tenant,docid,'approved' if approve else 'rejected',actor,{'changes':fields})
            self.db.commit()
            return self.get(tenant,docid)
    def audit(self,tenant):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM audit WHERE tenant=? ORDER BY id',(tenant,)).fetchall()]
    def verify_audit(self,tenant):
        chain=self.audit(tenant); prev='GENESIS'
        for row in chain:
            detail=json.loads(row['detail'])
            payload=json.dumps([tenant,row['doc_id'],row['action'],row['actor'],detail,row['at'],prev],separators=(',',':'),sort_keys=True)
            if row['prev_hash']!=prev or hashlib.sha256(payload.encode()).hexdigest()!=row['hash']:
                return False
            prev=row['hash']
        return True
