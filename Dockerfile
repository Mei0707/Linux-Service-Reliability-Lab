FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus
WORKDIR /srv/lab
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 lab
COPY app app
COPY infra/gunicorn.conf.py infra/entrypoint.sh infra/
USER lab
EXPOSE 8000
ENTRYPOINT ["sh", "infra/entrypoint.sh"]
