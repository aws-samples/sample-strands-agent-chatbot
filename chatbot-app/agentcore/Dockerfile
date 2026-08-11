# Dockerfile for Agent Core - AWS Bedrock AgentCore Runtime
FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# Install system dependencies
# Note: Playwright needs minimal libs even for remote CDP connection
# LibreOffice and poppler for document preview (Word/PPT/Excel → PDF → Image)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ca-certificates \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libreoffice-writer \
    libreoffice-impress \
    poppler-utils \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
# Note: bedrock-agentcore version is pinned in requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install nova-act separately with --no-deps to avoid strands-agents version conflict
# Dependencies for nova-act are already in requirements.txt (numpy, pandas, pillow, playwright, etc.)
RUN pip install --no-cache-dir --no-deps nova-act==3.1.263

# Copy application code and skill definitions
COPY src/ ./src/
COPY skills/ ./skills/
COPY config/ ./config/

# Expose port (AgentCore Runtime will map this)
EXPOSE 8080

# Health check endpoint for AgentCore Runtime
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Run with OpenTelemetry auto-instrumentation
CMD ["opentelemetry-instrument", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
