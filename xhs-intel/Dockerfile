FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY xhs-intel/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY xhs-intel/*.py /app/
COPY xhs-source /opt/xhs
WORKDIR /opt/xhs
RUN npm install --omit=dev --ignore-scripts
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/opt/xhs
CMD ["python", "/app/edge_api.py"]
