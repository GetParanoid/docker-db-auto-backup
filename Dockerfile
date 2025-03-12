FROM python:3.11-slim

ENV SCHEDULE="0 0 * * *"  PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source https://github.com/GetParanoid/docker-db-auto-backup

RUN apt-get --yes update && apt-get --yes install git && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/db-auto-backup
RUN mkdir -p /var/backups

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./db-auto-backup.py .

CMD ["python3", "./db-auto-backup.py"]
