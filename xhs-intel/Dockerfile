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
RUN if [ "$REUSE_BASE" != "true" ]; then pip install --no-cache-dir -r /app/requirements.txt; fi
COPY xhs-intel/*.py /app/
COPY xhs-source /opt/xhs
WORKDIR /opt/xhs
RUN if [ "$REUSE_BASE" != "true" ]; then npm ci --omit=dev --ignore-scripts --no-audit --no-fund; fi
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/opt/xhs
CMD ["python", "/app/edge_api.py"]
