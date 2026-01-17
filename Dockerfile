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

WORKDIR /build

# Cache dependencies: copy only Cargo files first
COPY rust/q2api-core/Cargo.toml rust/q2api-core/Cargo.lock* /build/

# Create dummy source to build dependencies
RUN mkdir -p src && echo "pub fn dummy() {}" > src/lib.rs

# Build dependencies only (cached unless Cargo.toml changes)
RUN cargo build --release || true

# Copy actual source and rebuild
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
COPY frontend/ /app/frontend/

# Expose port 8000
EXPOSE 8000

# Environment
ENV PYTHONUNBUFFERED=1

# Run application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
