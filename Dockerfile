FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[dashboard,user-session]" \
    && useradd --system --uid 10001 --create-home forwarder \
    && mkdir -p /data /session \
    && chown -R forwarder:forwarder /app /data /session

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "forwarder"]
