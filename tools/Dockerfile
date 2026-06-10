# Modern, pinned Python for the OntoRAG build/query tooling — independent of the
# host's Python (which may be old). The repo is bind-mounted at /work by compose,
# so outputs land back on the host. Embeddings are produced by talking to an
# ollama server on the host (no API key, no weights baked into the image).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /work

COPY tools/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENTRYPOINT ["python"]
CMD ["tools/build.py", "--corpus", "/corpus"]
