FROM python:3.12-slim
ARG REVIEWER_COMMIT=unknown
ENV REVIEWER_COMMIT=${REVIEWER_COMMIT}
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.14 && uv sync --frozen --no-dev
ENTRYPOINT ["python", "-m", "src.evaluation.reviewbench"]
