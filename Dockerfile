FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 7823
# Single process (no gunicorn workers) so APScheduler fires reminders/summaries once.
CMD ["python", "app.py"]
