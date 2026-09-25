FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.135.1 uvicorn==0.42.0 fastembed==0.7.4
COPY deploy/embedding/server.py ./server.py
ENV EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
EXPOSE 8100
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8100"]
