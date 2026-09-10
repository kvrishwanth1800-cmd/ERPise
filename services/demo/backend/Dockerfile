FROM python:3.12-alpine
WORKDIR /app
RUN pip install --no-cache-dir kafka-python==2.0.3 psycopg[binary]==3.2.6
COPY services/demo/backend/app.py /app/app.py
COPY services/demo/backend/integrated /app/integrated
COPY services/demo/backend/migrations /app/migrations
COPY services/foundation/src/foundation /app/foundation
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/ready')"
CMD ["python", "-m", "integrated.main"]
