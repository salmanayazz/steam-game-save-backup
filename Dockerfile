FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    SGSB_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY app ./app

RUN useradd --uid 1000 --create-home app && mkdir /data && chown app /data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" || exit 1

ENTRYPOINT ["sh", "-c", "chown app:app /data && exec setpriv --reuid=app --regid=app --init-groups \"$@\"", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
