# Multi-stage build: the runtime image serves the FastAPI surface on
# port 8000 and runs as a non-root user.
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml constraints.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir --constraint constraints.txt . \
    && pip install --no-cache-dir --constraint constraints.txt uvicorn

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 agent
WORKDIR /app
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY src ./src
COPY migrations ./migrations
ENV PYTHONPATH=/app/src \
    SMB_SQLITE_PATH=/app/data/smb.db
RUN mkdir -p /app/data && chown -R agent:agent /app
USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "smb_commerce.api:app", "--host", "0.0.0.0", "--port", "8000"]
