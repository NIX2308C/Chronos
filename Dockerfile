FROM python:3.12-slim

# Unbuffered stdout/stderr so logs reach Cloud Logging as they happen instead of
# sitting in a buffer until the process exits (or gets killed).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app.
COPY . .

# Don't run the app as root. Cloud Run doesn't require it, but a container that
# never needs root shouldn't have it — it turns "attacker gets code execution"
# into a meaningfully smaller problem.
RUN useradd --create-home --uid 10001 chronos && chown -R chronos:chronos /app
USER chronos

# Cloud Run provides $PORT (defaults to 8080); waitress must bind 0.0.0.0.
ENV PORT=8080
# Waitress defaults to 4 threads, but Cloud Run sends up to 80 concurrent
# requests per instance and nearly every request here is IO-bound (waiting on
# Gemini, Pinecone, or Firestore rather than burning CPU). More threads means
# more of that waiting overlaps. Override with WAITRESS_THREADS if you change
# the instance size or the concurrency setting.
ENV WAITRESS_THREADS=16
CMD ["sh", "-c", "waitress-serve --host=0.0.0.0 --port=${PORT} --threads=${WAITRESS_THREADS} app:app"]
