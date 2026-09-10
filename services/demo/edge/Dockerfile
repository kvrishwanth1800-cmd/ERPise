FROM python:3.12-alpine
RUN printf '%s\n' 'from http.server import BaseHTTPRequestHandler,HTTPServer' 'class H(BaseHTTPRequestHandler):' ' def do_GET(self): self.send_response(200);self.end_headers();self.wfile.write(b"ready")' 'HTTPServer(("0.0.0.0",8090),H).serve_forever()' > /edge.py
HEALTHCHECK --interval=5s --timeout=3s --retries=20 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090')"
CMD ["python", "/edge.py"]
