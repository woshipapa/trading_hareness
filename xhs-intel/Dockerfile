ARG BASE_IMAGE=python:3.11-slim
ARG REUSE_BASE=false
FROM ${BASE_IMAGE}
ARG REUSE_BASE=false

WORKDIR /app
RUN if [ "$REUSE_BASE" != "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
      && rm -rf /var/lib/apt/lists/*; \
    fi
COPY xhs-intel/requirements.txt /app/requirements.txt
# A reused XHS base already contains the OS/node layer, but Python dependency
# pins still need to be reconciled when requirements change (for example the
# Creator API's curl_cffi browser profile). Keep this install outside the OS
# package guard so a local base image can be rebuilt without apt/Docker Hub.
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY xhs-intel/collector.py xhs-intel/common.py xhs-intel/config.py xhs-intel/edge_api.py xhs-intel/local_worker.py xhs-intel/operations.py xhs-intel/store.py /app/
COPY xhs-intel/dashboard /app/dashboard
COPY xhs-source /opt/xhs
WORKDIR /opt/xhs
RUN if [ "$REUSE_BASE" != "true" ]; then npm ci --omit=dev --ignore-scripts --no-audit --no-fund; fi
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/opt/xhs
CMD ["python", "/app/edge_api.py"]
