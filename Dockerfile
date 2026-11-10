FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY docintel/ docintel/
COPY static/ static/
RUN useradd -u 10001 -m app && mkdir -p /data && chown -R app:app /data /srv
USER app
EXPOSE 8000
CMD ["uvicorn", "docintel.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
