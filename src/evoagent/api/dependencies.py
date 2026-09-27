"""FastAPI 依赖项。"""

from typing import Annotated

from fastapi import Depends, Request

from evoagent.config import Settings
from evoagent.db.session import Database
from evoagent.projects.service import ProjectService
from evoagent.tasks.service import TaskService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_task_service(database: Annotated[Database, Depends(get_database)]) -> TaskService:
    return TaskService(database.session_factory)


def get_project_service(database: Annotated[Database, Depends(get_database)]) -> ProjectService:
    return ProjectService(database.session_factory)


SettingsDependency = Annotated[Settings, Depends(get_settings)]
DatabaseDependency = Annotated[Database, Depends(get_database)]
TaskServiceDependency = Annotated[TaskService, Depends(get_task_service)]
ProjectServiceDependency = Annotated[ProjectService, Depends(get_project_service)]
