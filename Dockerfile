FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Install Playwright Chromium with system dependencies
RUN playwright install --with-deps chromium

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3081"]
