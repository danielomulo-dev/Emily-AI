FROM python:3.11-slim

WORKDIR /workspace

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose health check port
EXPOSE 8000

# Run the bot
CMD ["python", "main.py"]
