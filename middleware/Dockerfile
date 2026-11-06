FROM python:3.11-slim
WORKDIR /app
COPY middleware/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
COPY middleware/ /app/middleware/
COPY timing_v9.py /app/timing_v9.py
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "middleware.app:app", "--host", "0.0.0.0", "--port", "8000"]
