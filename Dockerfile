FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# WeasyPrint needs Pango + HarfBuzz + Cairo for OpenType shaping (Bangla conjuncts).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libcairo2 \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Refresh Fontconfig cache so WeasyPrint can resolve installed Bengali fonts.
RUN fc-cache -f -v

# Entrypoint: run migrations then start server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

