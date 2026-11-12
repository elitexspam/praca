FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/var/data PYTHONUNBUFFERED=1
# 1 processo só: chamadas, Mesas e eventos ao vivo ficam na memória; threads cuidam das conexões SSE.
CMD gunicorn app:app -k gthread -w 1 --threads 200 --timeout 120 --keep-alive 75 -b 0.0.0.0:${PORT:-10000}
