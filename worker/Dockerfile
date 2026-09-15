FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/pyxie_core ./pyxie_core
COPY worker/worker ./worker

CMD ["python", "-m", "worker.main"]
