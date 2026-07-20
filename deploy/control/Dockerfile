FROM python:3.12-slim

RUN useradd --system --uid 10001 --create-home charon \
    && mkdir -p /data \
    && chown charon:charon /data

WORKDIR /app
COPY control_server.py /app/control_server.py

USER charon
EXPOSE 8787
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)"]

CMD ["python", "-u", "control_server.py", "--host", "0.0.0.0", "--port", "8787", "--data-dir", "/data"]
