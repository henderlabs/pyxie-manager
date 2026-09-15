FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/pyxie_core ./pyxie_core
COPY api/app ./app
COPY api/alembic.ini ./alembic.ini
COPY api/migrations ./migrations
COPY api/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Static Stage W4 provisioning templates (wrapper/dispatch/sudoers/installer)
# -- bundled into the downloadable "Connect a Host" kit. Deliberately NOT
# the generated keypair (that's per-target, minted and stored via the API,
# never baked into an image).
COPY api/host_maintenance_kit ./host_maintenance_kit

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" || exit 1

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
