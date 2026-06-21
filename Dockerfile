FROM python:3.11-slim

# Tesseract is required for the OCR step (offline text extraction from
# unstructured Bronze files - no LLM/API key involved).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p bronze silver catalog lineage mappings

ENV PYTHONPATH=/app/src
EXPOSE 8000

# Default command runs the API; docker-compose overrides this for the
# one-shot pipeline job.
CMD ["python3", "src/api.py"]
