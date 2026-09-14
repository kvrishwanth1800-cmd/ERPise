FROM python:3.12-alpine
WORKDIR /app
RUN pip install --no-cache-dir psycopg[binary]==3.2.6
COPY services/demo/edge/edge.py /app/edge.py
EXPOSE 8090
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"
CMD ["python", "/app/edge.py"]
