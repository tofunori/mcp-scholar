# Scholar MCP server (streamable HTTP). See docs/hosting.md.
FROM python:3.13-slim

RUN pip install --no-cache-dir uv==0.10.8

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH=/opt/venv/bin:$PATH \
    NLTK_DATA=/opt/nltk_data
RUN python -m nltk.downloader -q -d /opt/nltk_data stopwords punkt

COPY src ./src

# Unprivileged user; /data is the only writable path (keys, OAuth state).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin scholar \
    && mkdir /data && chown scholar /data
USER scholar
ENV SCHOLAR_HOST=0.0.0.0 \
    SCHOLAR_PORT=8323 \
    DATA_DIR=/data \
    FASTMCP_HOME=/data/fastmcp
VOLUME /data
EXPOSE 8323

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8323/health')"

CMD ["python", "-m", "src.server_http"]
