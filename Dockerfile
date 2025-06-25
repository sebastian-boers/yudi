FROM python:3.10-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

# Expose port 5000 (optional, but good practice)
EXPOSE 5000

# Run with gunicorn: 4 workers, bind to all interfaces on port 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
