FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# config.yaml, .env e data/ vengono montati come volume (vedi docker-compose.yml)
ENV HLXWATCH_CONFIG=/app/config.yaml
VOLUME ["/app/data"]
EXPOSE 8765

CMD ["python", "-m", "hlxwatch", "daemon"]
