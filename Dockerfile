# Use the official Python 3.12 image
FROM python:3.12-slim

# Ensure output goes straight to logs without buffering, so the app's messages
# (such as backups being made) appear in the Docker log straight away
ENV PYTHONUNBUFFERED=1

# Time zone data, so the TZ setting (e.g. TZ=Europe/London in
# docker-compose.yml) works and dates are worked out in local time
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements.txt into the container
COPY requirements.txt /app/requirements.txt

# Install the dependencies listed in requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the app's files into the container (anything listed in
# .dockerignore, such as the database and backups, is left out)
COPY . /app

# Expose port 5000 for the Flask app
EXPOSE 5000

# Command to run the Flask app
CMD ["python", "app.py"]
