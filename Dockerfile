FROM python:3.13-slim
# Optional local hybrid retrieval: --build-arg WITH_DENSE=1 downloads the public
# embedding model at build time (network only during the build) and verifies
# its checksums. RETRIEVAL selects the default mode: bm25 | hybrid | dense.
ARG WITH_DENSE=0
ARG RETRIEVAL=bm25
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 \
    NAVIGATOR_DB=/data/index/navigator.sqlite3 \
    NAVIGATOR_IMPORT_DIR=/data/originals \
    NAVIGATOR_EMBED_MODEL=/opt/models/multilingual-e5-small \
    NAVIGATOR_RETRIEVAL=${RETRIEVAL}
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-osd && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dense.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$WITH_DENSE" = "1" ]; then pip install --no-cache-dir -r requirements-dense.txt; fi
COPY tools ./tools
RUN if [ "$WITH_DENSE" = "1" ]; then python tools/fetch_model.py /opt/models/multilingual-e5-small; fi
COPY app ./app
COPY fixtures ./fixtures
COPY tests ./tests
COPY eval ./eval
RUN useradd --create-home --uid 10001 navigator && mkdir -p /data/index /data/originals /data/runs && chown -R navigator:navigator /data
USER navigator
EXPOSE 8000
# On start the service imports every PDF/DOCX/XLSX/ZIP in /data/originals
# (unchanged files are skipped by checksum), then serves on port 8000.
ENTRYPOINT ["python", "-m", "app"]
CMD ["serve"]
