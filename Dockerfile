FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app.
COPY . .

# Cloud Run provides $PORT (defaults to 8080); waitress must bind 0.0.0.0.
ENV PORT=8080
CMD ["sh", "-c", "waitress-serve --host=0.0.0.0 --port=${PORT} app:app"]
