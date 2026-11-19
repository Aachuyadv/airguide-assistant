FROM python:3.12-slim

WORKDIR /app

# Non-root security
RUN useradd -m -u 1000 appuser

COPY . /app

RUN chown -R appuser:appuser /app

USER appuser

ENV PORT=8000
ENV HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["python", "-m", "airguide.server"]
