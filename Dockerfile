# 1. Python bazasidan boshlaymiz
FROM python:3.11-slim

# 2. Ishchi katalog
WORKDIR /app

# 3. Backup uchun pg_dump kerak
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# 4. Python kutubxonalarini o'rnatamiz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. App fayllarini nusxalaymiz
COPY . .

# 6. Port
EXPOSE 8000

# 7. FastAPI serverni ishga tushiramiz
CMD ["uvicorn", "run:app", "--host", "0.0.0.0", "--port", "8000"]
