FROM python:3.13-slim

# OPA runs as a process inside this container (Policy design §3); pinned and checksummed.
ARG OPA_VERSION=1.9.0
ARG OPA_SHA256=66fa66f3b730b2fb086003863428b382b2898d343adb4b5dfab5598b4d739eed
ADD --checksum=sha256:${OPA_SHA256} \
    https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_amd64_static \
    /usr/local/bin/opa
RUN chmod 0755 /usr/local/bin/opa && opa version

RUN pip install --no-cache-dir uv==0.11.8

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /repo/backend
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn hospital_agent.api.app:app --host 0.0.0.0 --port 8000 --reload"]
