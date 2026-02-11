# ============================
# CAOS Agent - Production Image
# ============================
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --target=/deps .

# ============================
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r caos && useradd -r -g caos caos

# Copy dependencies and source
COPY --from=builder /deps /usr/local/lib/python3.11/site-packages/
COPY src/ ./src/

# Set environment
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Switch to non-root user
USER caos

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]

