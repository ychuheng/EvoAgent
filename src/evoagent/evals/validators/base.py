"""Validator 协议、稳定结果格式和显式注册表。"""

import time
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from evoagent.evals.schema import ValidatorSpec
from evoagent.trace.bundle import TraceBundle


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    validator: str
    version: str
    passed: bool
    evidence: dict[str, Any]
    failure_reason: str | None = None
    duration_ms: float


class Validator(Protocol):
    name: str
    version: str

    def validate(
        self, spec: ValidatorSpec, trace: TraceBundle
    ) -> tuple[bool, dict[str, Any], str | None]: ...


class FunctionValidator:
    def __init__(
        self,
        name: str,
        function: Callable[[dict[str, Any], TraceBundle], tuple[bool, dict[str, Any], str | None]],
        version: str = "1",
    ) -> None:
        self.name = name
        self.version = version
        self._function = function

    def validate(
        self, spec: ValidatorSpec, trace: TraceBundle
    ) -> tuple[bool, dict[str, Any], str | None]:
        return self._function(spec.parameters, trace)


class ValidatorRegistry:
    def __init__(self, validators: tuple[Validator, ...] = ()) -> None:
        self._validators = {validator.name: validator for validator in validators}
        if len(self._validators) != len(validators):
            raise ValueError("validator names must be unique")

    def run(self, spec: ValidatorSpec, trace: TraceBundle) -> ValidationResult:
        validator = self._validators.get(spec.name)
        if validator is None or validator.version != spec.version:
            raise ValueError(f"unsupported validator: {spec.name}@{spec.version}")
        started = time.perf_counter()
        passed, evidence, reason = validator.validate(spec, trace)
        return ValidationResult(
            validator=validator.name,
            version=validator.version,
            passed=passed,
            evidence=evidence,
            failure_reason=reason,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

    def supports(self, name: str, version: str = "1") -> bool:
        validator = self._validators.get(name)
        return validator is not None and validator.version == version

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._validators))
