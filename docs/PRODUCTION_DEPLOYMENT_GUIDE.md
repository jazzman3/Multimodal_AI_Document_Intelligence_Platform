# AISX Document Intelligence — Production Deployment and Portfolio Guide

Version: 1.0 | Baseline: 10 November 2026

## 1. Deployment maturity: what is genuinely delivered

This repository contains real executable service code, an HTML review dashboard, API-key authentication, single-deployment tenant binding, PDF extraction and image OCR, optional OpenAI VLM processing, schema validation, reviewer approve/reject workflows, audit hash chaining, Prometheus metrics, Dockerfile/Compose definitions, a GitHub Actions CI example, 34 locally passing automated tests, a nine-document synthetic evaluation, and localhost HTTP benchmarks. The local verification does not prove a full enterprise or cloud deployment. The optional live VLM path has NOT been tested against a provider. The system should only be used with synthetic or expressly authorized documents until strengthened.

## 2. Target production architecture

See `architecture.png`, `architecture.svg`, `architecture.dot` in this folder. Separate current capabilities from desired architecture: browser -> API gateway with TLS/OIDC -> FastAPI -> S3/object store + malware scanner -> durable queue -> isolated PDF/OCR/VLM processing workers -> PostgreSQL -> review app -> signed audit export and monitoring. Connect approved results to your existing Enterprise AI OS as a narrowly-scoped, read-only service. Production infrastructure below is a design and implementation checklist, not an already deployed environment.

## 3. Prerequisites

- Windows 11 or Ubuntu 24.04 LTS for local testing; Python 3.12; Docker Desktop/Engine; Git; VS Code.
- Tesseract 5.x and an English language model (Dockerfile already installs Linux binaries); Tesseract must be discoverable on PATH for host-run OCR tests.
- AWS account, a domain name, AWS CLI with restricted identity, an IAM administrator for initial infrastructure bootstrap, budget alerts, and a verified GitHub repository for a remote deployment.
- If using OpenAI vision processing, an API key with API budget limits and authorization to transmit test documents to the provider. Confirm provider data-handling terms before handling client information.

## 4. Local environment setup (Windows PowerShell)

1. Download and extract `AISX_Document_Intelligence_COMPLETE.zip` to `C:\AI-Projects\DocumentIntel_Production`.
2. Open the project root in VS Code. Confirm `Dockerfile`, `docker-compose.yml`, `.env.example`, `docintel/`, `tests/`, `benchmarks/`, `static/` exist.
3. Run `python --version` and `docker version`. Start Docker Desktop.
4. Generate three different secrets with `python -c "import secrets; print(secrets.token_urlsafe(32))"` three times.
5. Run `Copy-Item .env.example .env`. Edit `.env` and set `ANALYST_KEY`, `REVIEWER_KEY`, and `ADMIN_KEY` separately; set `TENANT_ID=demo` for local demonstration. Never commit `.env`.
6. Run `docker compose up --build -d`, followed by `docker compose ps` and `curl.exe http://127.0.0.1:8000/readyz`. The service must return `{"ready":true}`.
7. Open `http://127.0.0.1:8000`. Enter the analyst key and tenant `demo`. Upload `samples/invoice_digital.pdf` with `invoice` schema and `rules` provider; verify five fields and `needs_review` status.
8. Replace the analyst key with reviewer key, select the uploaded document, correct any fields if needed, click Approve. Verify `approved` and use **Verify tenant audit chain** to confirm `valid:true`.
9. Repeat with `samples/contract_scanned.pdf` and `samples/form.png`, and test rejection, invalid inputs, and wrong-role access.
10. For local development without Docker: create `.venv`, install `requirements-dev.txt`, set `DATABASE_PATH` to a writable local path, then run `python -m uvicorn docintel.api:app --host 127.0.0.1 --port 8000`. Set your credentials in the shell environment first; the Python process does not automatically load `.env`.
11. Stop services with `docker compose down`; avoid `docker compose down -v` unless you intentionally want to delete persisted data.

## 5. Reproduce tests and benchmarks

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe -m benchmarks.evaluate
.\.venv\Scripts\python.exe -m benchmarks.http_benchmark
```

The evaluation creates 9 synthetic samples across 3 document families and 3 formats (native PDF, scanned PDF, PNG). For each document it matches five expected values, reporting aggregate field and whole-document exact-match accuracy. The HTTP benchmark starts a real localhost Uvicorn server, sends 30 POST document-ingestion requests and 90 GET document-list requests via four concurrent clients, measures p50/p95/p99 and throughput, and shuts down the test server. These are machine- and workload-specific **synthetic local baselines**, not performance SLAs and not real-world multimodal evaluation. Test the vision provider independently and publish its separate results only after actual calls.

## 6. Enable optional OpenAI VLM extraction

1. Add `OPENAI_API_KEY` to a locally protected `.env` or preferably a cloud secrets manager. Set `VISION_MODEL` to a model your account can call that supports PDF/image inputs via Responses API.
2. Restart local Docker with `docker compose up -d --force-recreate`.
3. From the dashboard choose `OpenAI vision`, then upload a synthetic invoice, contract, or form.
4. Verify that output is JSON with expected schema keys, no invented fields, and `needs_review` status. Correct and approve with the reviewer credential; provider-proposed values are never automatically trusted.
5. Benchmark the VLM separately with a labelled evaluation dataset: exact field match, normalized field F1, document classification, OCR vs VLM error stratification, cost per page, API latency and refusal/error rate. Do not report the rules/OCR benchmark as a VLM benchmark.

## 7. Cloud deployment plan (AWS, secure single-tenant staging)

**Phase A — Infrastructure**

1. Create a private GitHub repository; commit source only. Push to the default branch; confirm `.env` and sample secrets are excluded. Enable protected main branch, code review and required CI checks.
2. Create a nonproduction AWS account or isolated AWS environment, budget alarms, CloudTrail, IAM least privilege, and isolated VPC with private subnets.
3. Create an ECR repository for immutable, SHA-tagged images. Use GitHub Actions AWS OIDC federation with an appropriately constrained IAM role; do not create long-lived AWS access keys in repository secrets.
4. Create RDS PostgreSQL in private subnets, encrypted with KMS, with automatic backups and a tested restore exercise. **Implement and test a PostgreSQL storage adapter and migrations before switching away from the supplied SQLite-only implementation.** Do not put SQLite into an autoscaled ECS cluster.
5. Create private S3 buckets with encryption, block public access, versioning/retention policies. **Implement a secure object-storage adapter**, quarantine and anti-malware checks; the supplied service currently does not persist original documents.
6. Create an SQS queue with dead-letter queue and visibility timeout. **Implement queued ingestion and a separate limited-concurrency worker service**, plus idempotency keys and processing-status endpoints. Do not claim queue support in the current build.

**Phase B — Identity and governance**

7. Integrate workforce OIDC/SSO and MFA (e.g., Cognito or your corporate IdP), verify JWT signature/audience/issuer and map staff identity to tenant and role **server-side**. Replace shared API keys. Enforce review separation-of-duties and recording of human reviewer identity.
8. Restrict each user/token to its tenant, validate all authorization in the storage layer, and test cross-tenant access. In the current code a server-configured `TENANT_ID` prevents switching tenants via caller-controlled headers, but this is **single-tenant**, not production multi-tenant authorization.
9. Add WAF, payload limits, per-user rate limits, malware scanning, parser sandboxing, document content disarm where practical, and data-loss prevention/redaction. Follow OWASP file-upload guidance.
10. Store API/provider keys in Secrets Manager, never in code or images; implement rotation and audit.

**Phase C — Container and HTTPS deployment**

11. Build image: `docker build -t documentintel:release .`; run local acceptance tests and container image vulnerability scanning. Publish signed/attested image to ECR with an immutable commit SHA.
12. Deploy a staging ECS Fargate service for API and a separate private worker task. Use an ALB with ACM TLS certificate and HTTPS listener; HTTPS-only from browser to ALB, private ECS tasks, and restricted egress.
13. Deploy a static frontend through CloudFront with authenticated backend access, or deliver frontend from protected FastAPI. Configure CSP and secure session cookies once SSO exists; the included local dashboard is not SSO integrated.
14. Use an infrastructure-as-code repo (Terraform or AWS CDK) to version VPC, subnets, IAM, RDS, ECS, ALB, logging, policies and rollback resources. The packaged Docker Compose runs only on loopback for local testing; it is not a production AWS rollout plan on its own.
15. Release to staging, perform integration checks, IAM negative tests, multi-document ingestion, OCR failures, queue recovery, backups, and restore tests. Enable canary/blue-green deployment with rollback thresholds before production traffic.

**Phase D — Operate and certify internally**

16. Route API/worker logs to CloudWatch or an OpenTelemetry collector; scrub document content/PII from logs. Export metrics for upload count, failures, OCR time, VLM time/cost, queue depth, reviewer turnaround, field accuracy and error budget.
17. Define SLOs only after representative load tests, e.g. successful processing rate, end-to-end document latency by page count, reviewer lead time, and unauthorized-access count. Implement alerting and runbooks.
18. Complete penetration review, dependency scanning, backup verification, reliability tests, incident runbook, privacy/retention assessment, and user acceptance testing with synthetic or authorized datasets.
19. Configure a real owned domain such as `documents.yourdomain.example`, verify DNS, TLS and `/readyz` from a client outside the VPC; capture screenshot, deployment revision ID, build artifact hash, uptime period and benchmark evidence. **Only after this can you claim a public cloud deployment.**

## 8. CI/CD acceptance pipeline

Packaged GitHub workflow runs Python tests, synthetic evaluation, and a Docker image build. Enhance it with lint/type checks, SAST, software composition analysis, license review, SBOM, image scanning, signed artifacts, staging deploy, end-to-end browser checks, explicit production environment approval, canary monitoring, and automatic rollback. No remote publishing or cloud deployment is performed by the supplied GitHub workflow.

## 9. Integration with Enterprise AI OS

The OS orchestrator may call `GET /api/v1/system/capabilities`, `GET /api/v1/documents` and `GET /api/v1/documents/{id}` with an authorized service identity. A department workflow can submit an invoice, route extracted fields for independent human review, and consume ONLY approved data. Never permit LLM agents to issue reviewer approvals themselves. For real deployment, use scoped OAuth access tokens or mTLS, signed event schemas, idempotency, and per-tenant authorization rather than passing role-shared API keys.

## 10. Portfolio demonstration (5–7 minutes)

1. Show editable architecture diagram and identify implemented and target components.
2. Upload a synthetic **digital invoice PDF** and show structured fields plus evidence.
3. Upload a synthetic **scanned contract PDF** and show OCR conversion.
4. Upload a synthetic **application form PNG** and show extraction.
5. Demonstrate malformed input rejection, wrong-role approval denial, reviewer correction and approval, and audit verification.
6. Show the 34-test output, full synthetic-evaluation JSON (including per-field results), and live HTTP benchmark with environment/latency context.
7. Distinguish local rules+OCR validation from external vision-language model behavior; show a real VLM run only if credentials are available and results have been recorded.
8. Demonstrate an authenticated service call from your AISX AI OS when integration is actually connected.

## 11. Benchmarks acceptance and truthful resume bullet

Baseline: **34 automated tests passed**. Synthetic data: **45/45 fields matched across 9 documents** (3 each digital PDFs, scanned PDFs, PNGs). This is a deliberately clean, tiny dataset and not production quality evidence. `benchmarks/http_results.json` captures measured localhost HTTP p50/p95/p99 and throughput with actual POST and GET requests.

Resume-safe description: **"Engineered and locally validated a modular multimodal document-processing platform supporting PDF and image OCR, structured invoice/contract/form extraction, schema validation, human-in-the-loop approvals, tenant-bound REST endpoints, audit integrity checks, container packaging, and reproducible API/evaluation benchmarks."**

Do not write "deployed to AWS", "production certified", "real-world extraction accuracy 100%", or "live VLM accuracy measured" until independently verified.

## 12. References

- PyMuPDF page methods: https://pymupdf.readthedocs.io/en/latest/page.html
- OpenAI Vision: https://developers.openai.com/api/docs/guides/images-vision
- OpenAI file inputs: https://developers.openai.com/api/docs/guides/file-inputs
- OWASP File Upload Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- FastAPI documentation: https://fastapi.tiangolo.com/
- AWS ECS: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/Welcome.html
