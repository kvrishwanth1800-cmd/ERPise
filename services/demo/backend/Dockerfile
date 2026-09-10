FROM python:3.12-alpine
WORKDIR /app
RUN pip install --no-cache-dir kafka-python==2.0.3 psycopg[binary]==3.2.6
COPY app.py /app/app.py
COPY migrations /app/migrations
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/ready')"
CMD ["python", "/app/app.py"]
