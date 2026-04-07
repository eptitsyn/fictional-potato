FROM python:3.11-slim

WORKDIR /code

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
COPY . .
RUN uv pip install --system --no-cache -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
