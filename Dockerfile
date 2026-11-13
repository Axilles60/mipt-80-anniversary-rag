FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 NAVIGATOR_DB=/data/index/navigator.sqlite3
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-osd && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY fixtures ./fixtures
COPY tests ./tests
COPY eval ./eval
RUN useradd --create-home --uid 10001 navigator && mkdir -p /data/index /data/originals /data/models /data/runs && chown -R navigator:navigator /data
USER navigator
EXPOSE 8000
ENTRYPOINT ["python", "-m", "app"]
CMD ["serve"]
