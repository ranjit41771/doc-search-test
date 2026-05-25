FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Run as a non-root user to limit blast radius if the container is compromised.
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# --reload is a dev-only flag; it watches the filesystem and must not run in production.
# docker-compose.yml overrides this CMD with --workers 4 for the production-like stack.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
