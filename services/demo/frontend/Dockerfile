FROM nginx:1.27-alpine
RUN apk add --no-cache curl
COPY index.html /usr/share/nginx/html/index.html
COPY health /usr/share/nginx/html/health
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=20 CMD curl --fail --silent http://127.0.0.1/health >/dev/null
