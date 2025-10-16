# 1. Python bazasidan boshlaymiz
FROM python:3.11-slim

# 2. Ishchi katalog
WORKDIR /app

# 3. Kutubxonalarni o‘rnatamiz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. App fayllarini nusxalaymiz
COPY . .

# 5. Port
EXPOSE 8000

# 6. FastAPI serverni ishga tushiramiz
CMD ["uvicorn", "run:app", "--host", "0.0.0.0", "--port", "8000"]
