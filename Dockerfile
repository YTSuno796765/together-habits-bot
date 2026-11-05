FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HABITS_DB_PATH=/data/habits.db

WORKDIR /app
COPY pyproject.toml README.md ./
COPY together_habits ./together_habits
RUN pip install . && mkdir -p /data

# Mount a persistent volume at /data so the SQLite database survives redeploys.
VOLUME ["/data"]
CMD ["python", "-m", "together_habits"]
