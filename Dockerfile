FROM python:3.12.9-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Playwright Chromium for Salesforce Experience Cloud scrapers
# Installed as root before switching to appuser

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

RUN mkdir -p /app/data/.flask_sessions

RUN useradd -m -u 1000 appuser \
    && chown -R appuser /app
USER appuser

EXPOSE 5060

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
