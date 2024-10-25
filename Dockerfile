# Use the official Python 3.12 image
FROM python:3.12-slim

# Ensure output goes straight to logs without buffering.  Enable if you need to see any print statements for Python
# ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements.txt into the container
COPY requirements.txt /app/requirements.txt

# Install the dependencies listed in requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the app's files into the container
COPY . /app

# Expose port 5000 for the Flask app
EXPOSE 5000

# Command to run the Flask app
CMD ["python", "app.py"]
