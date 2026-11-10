import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from .core import Store, DocumentError, FIELDS, MAX_UPLOAD_BYTES

REQUESTS=Counter('docintel_requests_total','Requests', ['route','status'])
LATENCY=Histogram('docintel_request_seconds','Request duration', ['route'])

class ReviewRequest(BaseModel):
    fields: dict[str,str | None] = Field(default_factory=dict)
    approve: bool

def create_app(store=None):
    app=FastAPI(title='AISX DocumentIntel',version='1.0.0')
    app.state.store=store or Store(os.getenv('DATABASE_PATH','/tmp/documentintel.db'))
    @app.middleware('http')
    async def metrics(request:Request,call_next):
        start=time.perf_counter()
        try:
            resp=await call_next(request)
            return resp
        finally:
            route=request.scope.get('route')
            label=route.path if route is not None else 'unknown'
            LATENCY.labels(label).observe(time.perf_counter()-start)
            REQUESTS.labels(label,'completed').inc()
    def auth(x_api_key: str = Header(default='')):
        demo=os.getenv('ALLOW_DEMO_KEYS','false').lower()=='true'
        keys={
            'analyst':os.getenv('ANALYST_KEY',''),
            'reviewer':os.getenv('REVIEWER_KEY',''),
            'admin':os.getenv('ADMIN_KEY',''),
        }
        if demo:
            keys={k:v or f'demo-{k}-only' for k,v in keys.items()}
        for role,key in keys.items():
            if key and secrets.compare_digest(key,x_api_key):
                return role
        raise HTTPException(401,'Invalid API key')
    def tenant(x_tenant_id: str=Header(default='demo')):
        import re
        if not re.fullmatch(r'[a-zA-Z0-9_-]{2,50}',x_tenant_id):
            raise HTTPException(400,'Invalid tenant identifier')
        configured = os.getenv('TENANT_ID','demo')
        if x_tenant_id != configured:
            raise HTTPException(403,'Tenant not authorized for this deployment')
        return x_tenant_id
    def require(role,allowed):
        if role not in allowed:
            raise HTTPException(403,'Insufficient role')
    @app.get('/')
    def root():
        return FileResponse(Path(__file__).resolve().parent.parent/'static/index.html')
    @app.get('/healthz')
    def health(): return {'status':'ok'}
    @app.get('/readyz')
    def ready():
        app.state.store.db.execute('SELECT 1')
        return {'ready':True}
    @app.get('/metrics')
    def monitor(role=Depends(auth)):
        require(role,('admin',))
        return Response(generate_latest(),media_type=CONTENT_TYPE_LATEST)
    @app.get('/api/v1/schemas')
    def schemas(role=Depends(auth)):
        return FIELDS
    @app.post('/api/v1/documents')
    async def ingest(file:UploadFile=File(...),kind:str=Form(...),provider:str=Form('rules'),role=Depends(auth),org=Depends(tenant)):
        require(role,('analyst','admin'))
        if kind not in FIELDS:
            raise HTTPException(422,'Invalid document kind')
        if provider not in ('rules','vision'):
            raise HTTPException(422,'Invalid provider')
        data=await file.read(MAX_UPLOAD_BYTES+1)
        try:
            return app.state.store.create(org,file.filename or 'upload',kind,data,role,provider)
        except DocumentError as exc:
            raise HTTPException(422,str(exc)) from exc
        except Exception:
            raise HTTPException(502,'Document processing failed; check server logs')
    @app.get('/api/v1/documents')
    def docs(role=Depends(auth),org=Depends(tenant)):
        return app.state.store.list(org)
    @app.get('/api/v1/documents/{docid}')
    def get(docid:str,role=Depends(auth),org=Depends(tenant)):
        doc=app.state.store.get(org,docid)
        if not doc:
            raise HTTPException(404,'Document not found')
        return doc
    @app.post('/api/v1/documents/{docid}/review')
    def review(docid:str,data:ReviewRequest,role=Depends(auth),org=Depends(tenant)):
        require(role,('reviewer','admin'))
        try: doc=app.state.store.review(org,docid,role,data.fields,data.approve)
        except DocumentError as exc: raise HTTPException(409,str(exc)) from exc
        if doc is None: raise HTTPException(404,'Document not found')
        return doc
    @app.get('/api/v1/audit')
    def audit(role=Depends(auth),org=Depends(tenant)):
        require(role,('reviewer','admin'))
        return app.state.store.audit(org)
    @app.get('/api/v1/audit/verify')
    def verify(role=Depends(auth),org=Depends(tenant)):
        require(role,('reviewer','admin'))
        return {'valid':app.state.store.verify_audit(org)}
    @app.get('/api/v1/system/capabilities')
    def capabilities(role=Depends(auth)):
        return {'service':'document-intelligence','version':'1.0.0','document_kinds':list(FIELDS),
                'providers':['rules','vision'],'review_required':True,'integration':'AISX Enterprise AI OS'}
    return app

app=create_app()
