FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

EXPOSE 8100

CMD ["uvicorn", "pipeline_engine.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
