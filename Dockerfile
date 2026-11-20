FROM python:3.13-slim

LABEL io.modelcontextprotocol.server.name="io.github.acidvegas/kanopy"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]==3.3.9"
COPY . .

ENV KANOPY_HOST=0.0.0.0

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('KANOPY_PORT', '8000') + '/health', timeout=4)"

CMD ["python", "-m", "kanopy.app"]
