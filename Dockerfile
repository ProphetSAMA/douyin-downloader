FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/Downloaded

VOLUME ["/app/Downloaded"]

EXPOSE 8000

ENTRYPOINT ["python", "run.py"]
CMD ["--serve", "--serve-port", "8000", "-c", "config.yml"]
