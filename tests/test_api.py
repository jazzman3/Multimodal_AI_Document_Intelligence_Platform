import pytest
from fastapi.testclient import TestClient
from docintel.api import create_app
from docintel.core import Store
from tests.test_core import pdf,INVOICE

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('ANALYST_KEY','test-analyst')
    monkeypatch.setenv('REVIEWER_KEY','test-reviewer')
    monkeypatch.setenv('ADMIN_KEY','test-admin')
    monkeypatch.setenv('TENANT_ID','acme')
    return TestClient(create_app(Store()))

def headers(role='analyst',tenant='acme'):
    return {'X-API-Key':'test-'+role,'X-Tenant-ID':tenant}

def upload(client, role='analyst',tenant='acme'):
    return client.post('/api/v1/documents',headers=headers(role,tenant),
        files={'file':('inv.pdf',pdf(INVOICE),'application/pdf')},data={'kind':'invoice'})

def test_health(client):assert client.get('/healthz').json()=={'status':'ok'}
def test_ready(client):assert client.get('/readyz').status_code==200
def test_anonymous_forbidden(client):assert client.get('/api/v1/documents').status_code==401
def test_upload(client):
    r=upload(client); assert r.status_code==200 and r.json()['fields']['total']=='124.50'
def test_upload_wrong_role(client):assert upload(client,'reviewer').status_code==403
def test_review_role_block(client):
    d=upload(client).json();r=client.post(f"/api/v1/documents/{d['id']}/review",headers=headers(),json={'fields':{},'approve':True})
    assert r.status_code==403

def test_review_and_double_approval(client):
    d=upload(client).json();url=f"/api/v1/documents/{d['id']}/review"
    assert client.post(url,headers=headers('reviewer'),json={'fields':{},'approve':True}).json()['status']=='approved'
    assert client.post(url,headers=headers('reviewer'),json={'fields':{},'approve':True}).status_code==409

def test_cross_tenant_denied(client):
    d=upload(client).json();assert client.get(f"/api/v1/documents/{d['id']}",headers=headers('analyst','other')).status_code==403

def test_invalid_tenant(client):assert client.get('/api/v1/documents',headers=headers('analyst','@@')).status_code==400
def test_audit_verify(client):
    upload(client)
    assert client.get('/api/v1/audit/verify',headers=headers('reviewer')).json()['valid']
def test_metrics_protected(client):
    assert client.get('/metrics',headers=headers()).status_code==403
    assert client.get('/metrics',headers=headers('admin')).status_code==200
def test_bad_upload(client):
    r=client.post('/api/v1/documents',headers=headers(),files={'file':('fake.pdf',b'not pdf')},data={'kind':'invoice'})
    assert r.status_code==422
def test_schema(client):assert 'invoice' in client.get('/api/v1/schemas',headers=headers()).json()
def test_capabilities(client):assert client.get('/api/v1/system/capabilities',headers=headers()).json()['review_required']
