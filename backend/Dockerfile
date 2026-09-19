FROM python:3.13-slim

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
