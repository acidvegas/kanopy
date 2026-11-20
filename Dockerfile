FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "psycopg[binary]==3.3.9"
COPY . .

CMD ["python", "-m", "kanopy.app"]
