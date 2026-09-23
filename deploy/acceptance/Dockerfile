FROM docker:28-cli AS docker-cli
FROM python:3.13-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/bin/docker
WORKDIR /workspace
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[dev]'
# 测试源码在运行时只读挂载，不把测试输出或本地凭据打进镜像。
CMD ["python", "-m", "pytest"]
