#!/bin/sh
set -eu
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
rm -f "$PROMETHEUS_MULTIPROC_DIR"/*.db /tmp/lab-draining
exec gunicorn -c infra/gunicorn.conf.py app.main:app
