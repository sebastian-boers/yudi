FROM python:3.10-slim

# Create non-root user
RUN useradd -m appuser

WORKDIR /app

# Install dependencies first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Change ownership
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
