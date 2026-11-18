#!/bin/sh
# Writes the SPA's runtime configuration from environment variables (see frontend/.env.example).
set -eu
cat > /usr/share/nginx/html/config.json <<JSON
{
  "apiBaseUrl": "${API_BASE_URL:-/api/v1}",
  "errorReporting": ${ERROR_REPORTING:-true},
  "runPollIntervalMs": ${RUN_POLL_INTERVAL_MS:-2000},
  "release": "${RELEASE:-dev}"
}
JSON
echo "runtime config written: $(cat /usr/share/nginx/html/config.json | tr -d '\n ')"
