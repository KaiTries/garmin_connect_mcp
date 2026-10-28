FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /bin/

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /home/app/.config/garmin \
 && chown -R app:app /home/app/.config
WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    GARMIN_MCP_TRANSPORT=streamable-http \
    GARMIN_MCP_HOST=0.0.0.0 \
    GARMIN_MCP_PORT=8000 \
    GARMIN_PERSONAL_API_KEY_FILE=/run/secrets/garmin_api_key \
    GARMIN_MCP_HTTP_TOKEN_FILE=/run/secrets/mcp_http_token \
    TZ=Europe/Zurich

USER app

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status != 200)"
CMD ["garmin-pulse-mcp"]
