FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress libreoffice-writer fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.32.1 python-multipart==0.0.20
WORKDIR /app
COPY app.py .
RUN useradd -m worker && chown -R worker /app
USER worker
EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
