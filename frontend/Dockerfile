# syntax=docker/dockerfile:1
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

FROM nginx:1.27-alpine AS runtime
# Runtime configuration: config.json is written from environment variables at start-up
# (docker/40-runtime-config.sh), and the API upstream is templated into nginx.conf.
ENV BACKEND_UPSTREAM=backend:8000 \
    API_BASE_URL=/api/v1 \
    ERROR_REPORTING=true \
    RUN_POLL_INTERVAL_MS=2000 \
    RELEASE=1.0.0
COPY docker/default.conf.template /etc/nginx/templates/default.conf.template
COPY docker/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build /app/dist/frontend/browser /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
