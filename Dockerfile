FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force cache invalidation for config.yml
COPY config.yml /tmp/config.yml
RUN rm /tmp/config.yml

COPY . .

RUN mkdir -p /app/Downloaded

VOLUME ["/app/Downloaded"]

ENTRYPOINT ["python", "run.py"]
CMD ["-c", "config.yml"]
