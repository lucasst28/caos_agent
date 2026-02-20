FROM python:3.11-slim

WORKDIR /app

# Install dependencies (versão simplificada sem ML/LLM)
COPY requirements_simple.txt .
RUN pip install --no-cache-dir -r requirements_simple.txt

# Copy application code
COPY shared/ ./shared/
COPY services/atlas_supervisor/ ./services/atlas_supervisor/

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8001

# Run the simplified API (no ML/LLM)
CMD ["uvicorn", "services.atlas_supervisor.app_simple:app", "--host", "0.0.0.0", "--port", "8001"]
