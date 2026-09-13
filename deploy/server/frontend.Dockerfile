# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-build

WORKDIR /workspace/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/index.html ./index.html
COPY frontend/public ./public
COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/vite.config.ts ./vite.config.ts
COPY frontend/src ./src

RUN npm run build


FROM nginxinc/nginx-unprivileged:1.30-alpine AS frontend-runtime

USER root

COPY deploy/server/frontend-nginx.conf /etc/nginx/nginx.conf
COPY deploy/server/frontend-entrypoint.sh /usr/local/bin/frontend-entrypoint.sh
COPY --from=frontend-build --chown=101:101 /workspace/frontend/dist/ /usr/share/nginx/html/

RUN mkdir -p \
      /tmp/nginx/client_temp \
      /tmp/nginx/proxy_temp \
      /tmp/nginx/fastcgi_temp \
      /tmp/nginx/uwsgi_temp \
      /tmp/nginx/scgi_temp \
    && chown -R 101:101 /tmp/nginx /usr/share/nginx/html \
    && chmod 0555 /usr/local/bin/frontend-entrypoint.sh \
    && chmod 0444 /etc/nginx/nginx.conf

USER 101:101

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/frontend-entrypoint.sh"]
