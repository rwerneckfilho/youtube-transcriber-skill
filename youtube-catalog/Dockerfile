FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS whisper-build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake pkg-config build-essential libopenblas-dev && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch v1.9.3 https://github.com/ggml-org/whisper.cpp.git /src \
    && test "$(git -C /src rev-parse HEAD)" = "371b5a7561823ab2bb32142d2751e35e7534727b" \
    && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
       -DGGML_NATIVE=OFF -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS \
       -DWHISPER_BUILD_TESTS=OFF -DWHISPER_CURL=OFF \
    && cmake --build /build --target whisper-cli -j 4

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 TRANSCRIPTS_DIR=/storage/transcripts \
    DATA_DIR=/storage/catalog STATIC_DIR=/app/static MODEL_DIR=/storage/models \
    WORK_DIR=/storage/work XDG_CACHE_HOME=/storage/cache OPENBLAS_NUM_THREADS=1 \
    WHISPER_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl ffmpeg libgomp1 libopenblas0-pthread \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /storage/catalog /storage/transcripts /storage/models /storage/work /storage/cache \
    && chown -R 1000:1000 /storage
WORKDIR /app
COPY --from=whisper-build /build/bin/whisper-cli /usr/local/bin/whisper-cli
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend /build/dist ./static/
USER 1000:1000
EXPOSE 8765
ENTRYPOINT ["sh", "/app/scripts/start.sh"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--no-access-log"]
