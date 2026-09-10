FROM python:3.12-alpine
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/ready')"
CMD ["python", "/app/app.py"]
