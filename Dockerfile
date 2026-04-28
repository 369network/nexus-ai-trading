# =============================================================================
# NEXUS ALPHA — Multi-stage Dockerfile
# Base: python:3.12-slim
# Stage 1: builder — installs TA-Lib C library + Poetry deps
# Stage 2: development — adds dev tools + debugger
# Stage 3: production — lean final image, non-root user
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Install system build dependencies (TA-Lib C library + build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    gcc \
    g++ \
    make \
    automake \
    autoconf \
    libtool \
    pkg-config \
    libssl-dev \
    libffi-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Build and install TA-Lib C library from source (required by ta-lib Python pkg)
ARG TALIB_VERSION=0.4.0
RUN wget -q "http://prdownloads.sourceforge.net/ta-lib/ta-lib-${TALIB_VERSION}-src.tar.gz" \
    && tar -xzf "ta-lib-${TALIB_VERSION}-src.tar.gz" \
    && cd ta-lib \
    && ./configure --prefix=/usr --build=x86_64-linux-gnu \
    && make -j$(nproc) \
    && make install \
    && cd .. \
    && rm -rf ta-lib "ta-lib-${TALIB_VERSION}-src.tar.gz"

# Install Poetry
ENV POETRY_VERSION=1.8.2
ENV POETRY_HOME=/opt/poetry
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VENV_IN_PROJECT=1
ENV POETRY_CACHE_DIR=/tmp/poetry-cache

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install production dependencies (no dev group)
RUN poetry install --only=main --no-root --no-ansi \
    && rm -rf "${POETRY_CACHE_DIR}"

# ---------------------------------------------------------------------------
# Stage 2: development
# ---------------------------------------------------------------------------
FROM builder AS development

# Install dev dependencies too
RUN poetry install --no-root --no-ansi \
    && rm -rf "${POETRY_CACHE_DIR}"

# Install watchfiles for hot-reload
RUN poetry run pip install --no-cache-dir watchfiles debugpy

COPY . .
RUN poetry install --no-ansi

# Set development environment
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8080 8081 5678

CMD ["python", "-m", "src.main"]

# ---------------------------------------------------------------------------
# Stage 3: production
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS production

# Install only runtime libraries (not build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled TA-Lib C library from builder stage
COPY --from=builder /usr/lib/libta_lib* /usr/lib/
COPY --from=builder /usr/include/ta-lib /usr/include/ta-lib

# Update shared library cache
RUN ldconfig

# Create non-root user
RUN groupadd --gid 1001 nexus \
    && useradd --uid 1001 --gid nexus --shell /bin/bash --create-home nexus

WORKDIR /app

# Copy virtual environment from builder (avoid reinstalling)
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY --chown=nexus:nexus src/ ./src/
COPY --chown=nexus:nexus config/ ./config/
COPY --chown=nexus:nexus scripts/ ./scripts/
COPY --chown=nexus:nexus pyproject.toml ./

# Create required runtime directories
RUN mkdir -p /app/logs /app/.nexus \
    && chown -R nexus:nexus /app/logs /app/.nexus

# Switch to non-root user
USER nexus

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health', timeout=5)" || exit 1

EXPOSE 8080 8081

CMD ["python", "-m", "src.main"]
