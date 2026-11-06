# Imagen ligera con Python
FROM python:3.12-slim

# Carpeta de trabajo dentro del contenedor
WORKDIR /app

# Copiamos el agente
COPY agents/edge_agent.py .
COPY synthetic_process.py .
COPY timing_v9.py .

# Instalamos dependencias mínimas
RUN pip install --no-cache-dir aiomqtt requests

# Comando por defecto
ENTRYPOINT ["python", "edge_agent.py"]
