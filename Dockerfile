FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir .

# Default config — override at runtime via SNOWSTORM_MCP_CONFIG env var
# or mount a custom config file.
COPY example-configs/config.public-snowstorm.yaml /app/config.yaml
ENV SNOWSTORM_MCP_CONFIG=/app/config.yaml

# Bind to 0.0.0.0 so the container is reachable from outside.
# FastMCP reads host/port from FASTMCP_HOST / FASTMCP_PORT env vars.
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000

EXPOSE 8000

CMD ["snowstorm-mcp-server", "--transport", "streamable-http"]
