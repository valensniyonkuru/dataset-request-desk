FROM python:3.12-slim

# No .pyc files in the image; print logs immediately instead of buffering them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first, so this layer stays cached when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini pytest.ini ./
COPY --chmod=755 entrypoint.sh ./
COPY app ./app
COPY migrations ./migrations
COPY tests ./tests

# Run as an unprivileged user, not root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
# --no-access-log: our own middleware already logs every request as JSON.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
