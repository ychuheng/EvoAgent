"""EvoAgent API 的命令行启动入口。"""

import uvicorn

from evoagent.api.app import create_app
from evoagent.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.value.lower(),
    )
