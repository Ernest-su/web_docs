FROM python:3.14-slim

ENV WEB_DOCS_HOST=0.0.0.0 \
    WEB_DOCS_PORT=8090 \
    WEB_DOCS_TITLE=在线文档 \
    WEB_DOCS_GIT_PULL=1 \
    WEB_DOCS_GIT_PULL_INTERVAL=300 \
    WEB_DOCS_GIT_PULL_TIMEOUT=120 \
    WEB_DOCS_ROOT=/docs \
    HOME=/tmp \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY serve-docs.py /app/serve-docs.py
COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py

EXPOSE 8090

ENTRYPOINT ["python", "/usr/local/bin/docker-entrypoint.py"]
CMD ["python", "/app/serve-docs.py", "--no-open"]
