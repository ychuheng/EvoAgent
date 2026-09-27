"""授权项目（M1）：根规范化、授权级别与数据库投影。"""

from evoagent.projects.schema import (
    MAX_ROOT_LENGTH,
    NormalizedRoot,
    ProjectAuthorization,
    ProjectAuthorizationError,
    ProjectAuthorizationRevoked,
    ProjectNotFoundError,
    ProjectRegistrationError,
    ProjectStatus,
    ensure_candidate_inside_root,
    normalize_root,
    resolve_inside_root,
)

__all__ = [
    "MAX_ROOT_LENGTH",
    "NormalizedRoot",
    "ProjectAuthorization",
    "ProjectAuthorizationError",
    "ProjectAuthorizationRevoked",
    "ProjectNotFoundError",
    "ProjectRegistrationError",
    "ProjectStatus",
    "ensure_candidate_inside_root",
    "normalize_root",
    "resolve_inside_root",
]
