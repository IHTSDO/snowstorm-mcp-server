FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir . \
    && useradd --system --no-create-home appuser

# Default config — override at runtime via SNOWSTORM_MCP_CONFIG env var
# or mount a custom config file.
COPY example-configs/config.public-snowstorm.yaml /app/config.yaml
ENV SNOWSTORM_MCP_CONFIG=/app/config.yaml

# Bind to 0.0.0.0 so the container is reachable from outside.
# FastMCP reads host/port from FASTMCP_HOST / FASTMCP_PORT env vars.
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000

EXPOSE 8000

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('localhost',8000),timeout=3); s.close()" || exit 1

CMD ["snowstorm-mcp-server", "--transport", "streamable-http"]
