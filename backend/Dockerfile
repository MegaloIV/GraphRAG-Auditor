FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# requirements.txt está en UTF-16 (generado en Windows); iconv lo convierte a UTF-8.
# grep excluye torch para instalar la variante CPU-only (~200MB vs ~2GB de CUDA).
RUN iconv -f UTF-16 -t UTF-8 requirements.txt | grep -v "^torch==" > req.txt \
    && pip install --no-cache-dir -r req.txt \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]