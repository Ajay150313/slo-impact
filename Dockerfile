FROM python:3.12-slim

LABEL org.opencontainers.image.title="slo-impact" \
      org.opencontainers.image.description="User-impact-weighted SLO dashboard" \
      org.opencontainers.image.source="https://github.com/Ajay150313/slo-impact" \
      org.opencontainers.image.licenses="MIT"

# Non-root user for production best practice
RUN groupadd -r sloimpact && useradd -r -g sloimpact sloimpact

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY slo_impact/ ./slo_impact/
COPY static/     ./static/

# Ownership
RUN chown -R sloimpact:sloimpact /app
USER sloimpact

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["python", "-m", "slo_impact.main"]
