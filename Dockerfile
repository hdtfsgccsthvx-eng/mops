FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fa_base ./fa_base
COPY tests ./tests
RUN pip install --no-cache-dir httpx && python -m compileall -q fa_base && python -c 'import fa_base.bot; import fa_base.server' && python -m unittest discover -s tests -v
CMD ["python", "-m", "fa_base.server"]