# AISX Document Intelligence — v1.0

**Run-tested, local-first document intelligence platform** with PDF text extraction, scanned-document OCR, invoice/contract/form field extraction, schema validation, human approval, audit hash chains, REST APIs, Prometheus instrumentation, an HTML dashboard, Docker packaging, and optional OpenAI vision-model extraction.

> **Status:** 34 passing local tests; evaluated on nine synthetic documents; localhost HTTP benchmark completed. **Not deployed to cloud, not enterprise production certified.** The offline path is rule-based extraction + Tesseract OCR; VLM integration is implemented but requires an API key and separate evaluation.

## Quick start (Windows PowerShell)

1. Install Python 3.12, Tesseract OCR, Docker Desktop, and Git. Restart your terminal after installing Tesseract.
2. Extract this ZIP, then open its `DocumentIntel_Production` directory in VS Code.
3. Copy `.env.example` to `.env`. Generate three different keys using `python -c "import secrets; print(secrets.token_urlsafe(32))"` three times; populate `ANALYST_KEY`, `REVIEWER_KEY`, `ADMIN_KEY` and keep `TENANT_ID=demo`.
4. Start locally:

```powershell
Copy-Item .env.example .env
# Edit .env, set three distinct API keys

docker compose up --build -d
curl.exe http://127.0.0.1:8000/healthz
```

5. Open <http://127.0.0.1:8000>, paste your analyst key, choose a schema, and upload a sample PDF or image. Switch to reviewer key to approve/reject corrected data.
6. Run tests and evaluations:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe -m benchmarks.evaluate
.\.venv\Scripts\python.exe -m benchmarks.http_benchmark
```

**Note:** Host-based OCR evaluation also requires a working local `tesseract` binary. CI installs it. Docker image installs it. On Linux/macOS use `python3` and `source .venv/bin/activate`.

## Features

- Upload: PDF, PNG, JPEG, WEBP. Bounded upload size, PDF page count and image pixels.
- Extraction: digital PDF text, scanned PDF OCR, image OCR; deterministic schema extraction; optional OpenAI vision input using Responses API.
- Schemas: invoice, contract, form. Five required fields per schema.
- Validation: missing values, ISO date formats/calendar validity, email syntax, nonnegative invoice totals.
- Review: field corrections, mandatory approve/reject action with separate reviewer credentials.
- Governance: no approval is automatic, append-only-by-API hash-chained audit records, no storage of source document bytes.
- Storage: persistent SQLite database mounted in a Docker named volume (single-instance only).
- Metrics: `/metrics`, admin key required.
- Integration: `/api/v1/system/capabilities`, GET reviewed documents; AI OS can connect via tenant-scoped credentials.

## Limitations and production blockers

- API-key role authorization, not OIDC, SSO, MFA or real human identity. Keys are scoped to a single configured tenant; **do not use one deployment to host multiple customer tenants**.
- Sync document processing in API process. For production workloads use object storage + durable queue + isolated worker pool.
- SQLite cannot support horizontally scaled web replicas. Migrate to PostgreSQL with transactional audit logging.
- No malware scanning, document disarm/reconstruction, external audit anchoring, centralized secrets rotation, DLP, or immutable retention.
- The rule-based extraction baseline is **not** a vision-language model; VLM route is optional and untested against a live provider here.
- No supported public/cloud URL exists yet. Use controlled non-sensitive demo data only.
- Field confidence values are heuristic from rules; vision provider returns null confidence; never interpret them as calibrated probabilities.

See `docs/PRODUCTION_DEPLOYMENT_GUIDE.md` for deployment plan, security gates, integration, and portfolio checklist.

## Repository map

```text
docintel/                 FastAPI API + extraction + storage
static/index.html         Browser UI
tests/                    Pytest automated checks
benchmarks/               Synthetic evaluation and live HTTP benchmark
docs/architecture.{png,svg,dot}
docs/PRODUCTION_DEPLOYMENT_GUIDE.md
samples/                  Generated, safe sample documents
Dockerfile
docker-compose.yml
.github/workflows/ci.yml
```

## API example

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/documents" `
  -H "X-API-Key: YOUR_ANALYST_KEY" -H "X-Tenant-ID: demo" `
  -F "kind=invoice" -F "provider=rules" -F "file=@samples/invoice_digital.pdf"
```

Next retrieve `/api/v1/documents` using analyst credentials; approve with reviewer credentials using `POST /api/v1/documents/{id}/review` with JSON `{"fields":{},"approve":true}`. The API's Swagger documentation is at `/docs`.

## Related technical references

- https://pymupdf.readthedocs.io/en/latest/page.html
- https://developers.openai.com/api/docs/guides/images-vision
- https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
