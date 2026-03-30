FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app.py pb_client.py ./
COPY ui/ ui/

ENV PYTHONUNBUFFERED=1
ENV ALLOWED_ORIGINS=*
# On Railway, POCKETBASE_URL will be set to the internal service URL
# e.g. http://pocketbase.railway.internal:8090

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 app:app"]
