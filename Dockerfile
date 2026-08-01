FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRIPSYNC_STATE_DIR=/app/state

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 tripsync
COPY --chown=tripsync:tripsync . ./
RUN mkdir -p /app/state && chown tripsync:tripsync /app/state

USER tripsync

# The application intentionally loads its semantic retrieval model from local
# files. Download it at image-build time so first user request is not delayed.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
