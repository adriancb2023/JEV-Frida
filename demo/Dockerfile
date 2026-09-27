FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi>=0.115 uvicorn>=0.30 pydantic>=2.9 python-multipart>=0.0.18 pypdf>=4
COPY server.py /app/server.py
COPY static/ /app/static/
COPY data/ /app/data/
ENV PYTHONUNBUFFERED=1 PORT=8010 KEV_URL=http://kev-api:9936 DATA_DIR=/app/data
EXPOSE 8010
CMD ["python", "server.py"]
