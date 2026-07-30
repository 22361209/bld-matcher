FROM python:3.12-slim AS app-source

WORKDIR /source

COPY . .

RUN set -eu; \
    revision=""; \
    if [ -f .git/HEAD ]; then \
      head_value="$(cat .git/HEAD)"; \
      case "$head_value" in \
        "ref: refs/heads/"*) ref_path="${head_value#ref: }"; revision="$(cat ".git/$ref_path" 2>/dev/null || true)" ;; \
        *) revision="$head_value" ;; \
      esac; \
    fi; \
    if printf '%s' "$revision" | grep -Eq '^[0-9a-fA-F]{40}$'; then \
      printf '%s\n' "$revision" | cut -c1-7 > /deployment-version; \
    else \
      printf 'unknown\n' > /deployment-version; \
    fi; \
    rm -rf .git

FROM scratch AS version-artifact

COPY --from=app-source /deployment-version /deployment-version

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=app-source /source/ .
COPY --from=version-artifact /deployment-version /app/.deployment-version

RUN mkdir -p /app/data /app/uploads /app/outputs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).close()"

CMD ["sh", "-c", "python -m scripts.init_database && exec gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 120 wsgi:app"]
