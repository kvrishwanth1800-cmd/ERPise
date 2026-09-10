FROM nginx:1.27-alpine
COPY index.html /usr/share/nginx/html/index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD wget -q -O /dev/null http://localhost/
