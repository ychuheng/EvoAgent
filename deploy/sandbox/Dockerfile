ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}
COPY src/evoagent/sandbox/guest.py /opt/evoagent/runner.py
RUN mkdir /input /output && chmod 0555 /input && chown 10001:10001 /output
USER 10001:10001
WORKDIR /output
ENTRYPOINT ["/usr/local/bin/python", "/opt/evoagent/runner.py"]
