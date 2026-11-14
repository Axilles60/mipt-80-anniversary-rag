# Build stage for the optional local generator: llama-cpp-python is published
# as source only, so it is compiled here and only the wheel reaches the image.
FROM python:3.13-slim AS llama-build
ARG WITH_RAG=0
ARG LOCAL_LLM=1
COPY requirements-rag.txt /tmp/
RUN mkdir -p /wheels && if [ "$WITH_RAG" = "1" ] && [ "$LOCAL_LLM" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends build-essential cmake && \
      pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements-rag.txt; \
    fi

FROM python:3.13-slim
# Optional local hybrid retrieval: --build-arg WITH_DENSE=1 downloads the public
# embedding model at build time (network only during the build) and verifies
# its checksums. RETRIEVAL selects the default mode: bm25 | hybrid | dense.
# Optional RAG answer: --build-arg WITH_RAG=1 (implies the embedding model) adds
# the cross-encoder bge-reranker-v2-m3 and the local generator
# Qwen3-4B-Instruct-2507 q4_k_m (llama.cpp on CPU); PIPELINE=rag makes it the
# default answer. LOCAL_LLM=0 leaves the generator out of this image: it then
# runs on the GPU in the llama.cpp service of docker-compose.yml (profile gpu).
# Recommended: docker compose --profile gpu up --build (see README).
ARG WITH_DENSE=0
ARG WITH_RAG=0
ARG LOCAL_LLM=1
ARG RETRIEVAL=bm25
ARG PIPELINE=extractive
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 \
    NAVIGATOR_DB=/data/index/navigator.sqlite3 \
    NAVIGATOR_IMPORT_DIR=/data/originals \
    NAVIGATOR_EMBED_MODEL=/opt/models/multilingual-e5-small \
    NAVIGATOR_RERANK_MODEL=/opt/models/bge-reranker-v2-m3 \
    NAVIGATOR_LLM=/opt/models/qwen3-4b-instruct-2507/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
    NAVIGATOR_RETRIEVAL=${RETRIEVAL} \
    NAVIGATOR_PIPELINE=${PIPELINE}
WORKDIR /app
# libgomp1: OpenMP runtime of llama.cpp (small; harmless without the generator).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-osd libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dense.txt requirements-rag.txt ./
COPY --from=llama-build /wheels /wheels
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$WITH_DENSE" = "1" ] || [ "$WITH_RAG" = "1" ]; then pip install --no-cache-dir -r requirements-dense.txt; fi && \
    if [ "$WITH_RAG" = "1" ] && [ "$LOCAL_LLM" = "1" ]; then pip install --no-cache-dir --no-index /wheels/*.whl; fi && \
    rm -rf /wheels
COPY tools ./tools
RUN if [ "$WITH_DENSE" = "1" ] || [ "$WITH_RAG" = "1" ]; then python tools/fetch_model.py e5 /opt/models/multilingual-e5-small; fi && \
    if [ "$WITH_RAG" = "1" ]; then python tools/fetch_model.py reranker /opt/models/bge-reranker-v2-m3; fi && \
    if [ "$WITH_RAG" = "1" ] && [ "$LOCAL_LLM" = "1" ]; then python tools/fetch_model.py qwen3 /opt/models/qwen3-4b-instruct-2507; fi
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
