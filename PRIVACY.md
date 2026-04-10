# Privacy Policy

**snowstorm-mcp-server** — maintained by [SNOMED International](https://www.snomed.org/)

Last updated: 2026-03-18

## What this server does

This MCP server proxies requests to a SNOMED CT terminology server (Snowstorm
or Snowstorm Lite). It translates MCP tool calls into FHIR and Snowstorm API
requests, returns the results, and does not store any data between requests.

## Data collection

This server does **not**:

- Collect, store, or process personal data
- Use cookies or local storage
- Log query content to persistent storage
- Send data to any third party beyond the configured SNOMED CT backend

All requests are forwarded to the configured Snowstorm backend and responses
are returned directly to the caller. No request or response content is retained
after the response is delivered.

### Per-session rate limiting

When per-session rate limiting is enabled in the server configuration, the
server maintains an in-memory record of call timestamps per MCP session for
the sole purpose of enforcing rate limits. This state contains no personally
identifiable information, no query content, and no response data. It is held
only in process memory and is automatically discarded when the session ends
(via weak references) or the server process stops. No session state is
persisted to disk or transmitted externally.

## Backend data

Responses contain SNOMED CT terminology content (concept codes, descriptions,
relationships) provided by the connected Snowstorm instance. SNOMED CT content
is subject to the [SNOMED International licensing terms](https://www.snomed.org/snomed-ct/get-snomed).

## Hosting

When deployed as a hosted connector, standard web server access logs (IP
address, timestamp, request path) may be retained by the hosting infrastructure
for operational purposes. These logs do not contain query parameters or request
bodies.

## More Information & Contact

For the full SNOMED International tooling privacy policy, please see [the privacy policy](https://snomed.org/snomedtools-privacy). 

For questions about this privacy policy, contact SNOMED International at
[info@snomed.org](mailto:info@snomed.org).
