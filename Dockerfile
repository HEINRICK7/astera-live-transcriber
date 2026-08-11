FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

ARG INSTALL_PARAKEET=false
RUN if [ "$INSTALL_PARAKEET" = "true" ]; then pip install '.[parakeet]'; else pip install .; fi

EXPOSE 8000

CMD ["uvicorn", "astera_live_transcriber.main:app", "--host", "0.0.0.0", "--port", "8000"]
