FROM ghcr.io/astral-sh/uv:0.12.21 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY dashboard ./dashboard
COPY scripts ./scripts
COPY .streamlit ./.streamlit
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 taskforge \
    && mkdir -p /app/data \
    && chown taskforge:taskforge /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER taskforge
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "taskforge_python.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
