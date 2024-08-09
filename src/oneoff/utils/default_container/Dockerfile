# Use a lightweight Python base image
FROM python:3.11-alpine

# Set working directory in the container
WORKDIR /app

# Copy requirements.txt to the working directory
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY main.py .

# Command to run your Python script
CMD ["python", "main.py"]
