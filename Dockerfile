# Optional cloud container for later (Railway / Render / Fly.io / VPS).
# Runs Pilot's password-protected Web App with an isolated Linux workspace, bash terminal,
# Python environment, and headless Chromium for browsing & PDF creation.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

ENV PILOT_HOME=/data
ENV PORT=8765
EXPOSE 8765

CMD ["python", "-m", "src", "web", "--host", "0.0.0.0"]
