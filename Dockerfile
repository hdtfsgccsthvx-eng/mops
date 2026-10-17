FROM python:3.12-slim
WORKDIR /app
COPY WALKIE_v23_1_DEPLOY.zip /tmp/walkie.zip
RUN python -c "from zipfile import ZipFile; ZipFile('/tmp/walkie.zip').extractall('/app')" && mkdir -p /data
ENV PORT=3000 PYTHONUNBUFFERED=1 DATA_DIR=/data DB_PATH=/data/walkie.sqlite3
EXPOSE 3000
CMD ["python", "-u", "app_v15.py"]
