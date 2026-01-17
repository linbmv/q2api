# Multi-stage build for Rust extension + Python application
# Stage 1: Build Rust extension
FROM python:3.10-slim AS rust-builder

# Install Rust and build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    pkg-config \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.cargo/bin:${PATH}"

# Install maturin
RUN pip install --no-cache-dir maturin

# Copy Rust project
WORKDIR /build
COPY rust/q2api-core/ /build/

# Build the wheel
RUN maturin build --release --strip

# Stage 2: Final application image
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install Rust wheel from builder stage
COPY --from=rust-builder /build/target/wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# Copy application code
COPY *.py /app/

# Expose port 8000
EXPOSE 8000

# Environment
ENV PYTHONUNBUFFERED=1

# Run application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
