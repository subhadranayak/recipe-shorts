# Recipe Shorts — free-tier web service (Render: 512 MB / 0.1 CPU).
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY recipe_shorts ./recipe_shorts

ENV HOST=0.0.0.0 \
    PORT=10000 \
    PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    DEFAULT_RESOLUTION=720 \
    LIGHT_MODE=1 \
    FFMPEG_THREADS=1 \
    FFMPEG_PRESET=veryfast \
    FFMPEG_CRF=20 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

EXPOSE 10000
CMD ["python", "-u", "app.py"]
