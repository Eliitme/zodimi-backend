# Build stage: pyswisseph has no prebuilt wheel for every platform and
# compiles from source (needs gcc).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Runtime stage: same base image so the venv's interpreter path stays valid.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /srv/app
COPY --from=builder /srv/app/.venv ./.venv
COPY app ./app

ENV PATH="/srv/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
