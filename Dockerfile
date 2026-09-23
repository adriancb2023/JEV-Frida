FROM python:3.13-slim

WORKDIR /app

# Instalar dependencias de sistema esenciales
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

# Instalar PyTorch compilado para CPU (ahorra más de 4 GB al evitar drivers NVIDIA CUDA)
RUN pip install --no-cache-dir torch>=2.6 --index-url https://download.pytorch.org/whl/cpu

# Instalar dependencias requeridas para la inferencia de KEV
RUN pip install --no-cache-dir \
    "transformers>=5.17,<6" \
    "peft>=0.21" \
    "accelerate>=1.15.0" \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "pydantic>=2.9" \
    "typesafe-sdk>=0.6.0" \
    "setuptools"

# Copiar el paquete kev
COPY kev/ /app/kev/

# Variables de entorno por defecto (CPU, fp32, alta fidelidad)
# PORT se alinea con el .env; el CMD/HEALTHCHECK lo leen dinámicamente
ENV PYTHONUNBUFFERED=1 \
    KEV_HOST=0.0.0.0 \
    PORT=9936 \
    KEV_MODEL=jaredpalmer/kev-0.8b \
    KEV_DATE_FACTS=1 \
    KEV_DTYPE=fp32

EXPOSE ${PORT}

# Verificación de salud: usa la misma variable PORT que el servidor
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -f http://localhost:${PORT}/v1/models || exit 1

# Arrancar el servidor FastAPI
CMD ["python", "-m", "kev.serve"]
