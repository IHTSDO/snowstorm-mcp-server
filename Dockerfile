# Turn uv.lock into a hash-pinned requirements file. A separate stage keeps uv
# out of the runtime image and works with the classic builder as well as
# BuildKit (the hosts' docker has no buildx).
FROM python:3.12-slim AS requirements
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --locked --no-dev --no-emit-project --format requirements-txt -o /requirements.txt

FROM python:3.12-slim AS base

WORKDIR /app

COPY --from=requirements /requirements.txt /tmp/requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

# Install exactly the runtime versions in uv.lock, hash-checked, so the image
# runs what CI tested. A bare `pip install .` took the newest releases on every
# build, which once shipped an SDK upgrade nobody had tested.
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps . \
    && rm /tmp/requirements.txt \
    && useradd --system --no-create-home appuser

# Default config — override at runtime via SNOWSTORM_MCP_CONFIG env var
# or mount a custom config file.
COPY example-configs/config.docker-snowstorm.yaml /app/config.yaml
ENV SNOWSTORM_MCP_CONFIG=/app/config.yaml

# Bind to 0.0.0.0 so the container is reachable from outside.
# FastMCP reads host/port from FASTMCP_HOST / FASTMCP_PORT env vars.
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000

EXPOSE 8000

USER appuser

# A TCP connect proves only that the kernel is accepting on the port — the
# backlog fills from the kernel side, so a wedged or swap-thrashing process
# still passes while serving nothing. This performs a real HTTP round-trip
# through the ASGI app, so the event loop has to be alive to answer it.
#
# It deliberately does not call an MCP method. Under the stateful session
# manager a tools/list would need an initialize handshake, and that would mint
# a session on every probe — 2,880 a day at this interval — feeding the very
# retention problem a healthcheck is meant to catch.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/favicon.ico', timeout=3).read()" || exit 1

CMD ["snowstorm-mcp-server", "--transport", "streamable-http"]
