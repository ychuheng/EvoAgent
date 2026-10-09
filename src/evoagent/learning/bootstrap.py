"""Lazy learning assembly; formal eval components are not loaded for personal jobs."""

from evoagent.learning.budget import LearningBudgetService
from evoagent.learning.configuration import candidate_configuration
from evoagent.learning.demo import PersonalDemoGenerator
from evoagent.learning.provider import LearningBudgetedProvider
from evoagent.learning.schema import LearningError
from evoagent.learning.worker import LearningJobHandler
from evoagent.skills.extraction import ModelCandidateGenerator
from evoagent.skills.validation import SkillDefinitionValidator
from evoagent.tools.catalog import default_skill_tool_catalog
from evoagent.workers.rate_limit import GatedProvider


def assemble_learning(factory, store, settings, service_gate):
    provider = None
    if (
        settings.learning_enabled
        and settings.provider.value != "mock"
        and settings.skill_extractor_model
    ):
        from evoagent.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            api_key=settings.api_key,
            base_url=str(settings.base_url),
            timeout_seconds=settings.model_timeout_seconds,
        )
    budget = LearningBudgetService(factory, settings)
    validator = SkillDefinitionValidator(
        default_skill_tool_catalog(),
        allowed_tools=frozenset(settings.skill_allowed_tools),
        max_steps=settings.skill_max_steps,
        max_risk=settings.skill_max_effective_risk,
        supported_schema_version=2,
    )

    def generator(request, guard):
        frozen = request.policy_snapshot.get("generator_configuration")
        if frozen is not None and frozen != candidate_configuration(settings):
            raise LearningError("learning_configuration_changed")
        if settings.provider.value == "mock":
            return PersonalDemoGenerator()
        if provider is None:
            raise LearningError("learning_model_not_configured")
        if frozen is None:
            raise LearningError("learning_configuration_unverified")

        async def check():
            async with factory() as session:
                await handler._check(session, guard, source_required=True)

        paid = LearningBudgetedProvider(
            provider, budget, request.id, guard, check=check, provider_name=settings.provider.value
        )
        return ModelCandidateGenerator(
            GatedProvider(paid, service_gate, "model", check),
            model=settings.skill_extractor_model,
            max_output_tokens=min(settings.model_request_max_output_tokens or 4096, 4096),
        )

    handler = LearningJobHandler(
        factory,
        store,
        generator,
        validator,
        learning_enabled=settings.learning_enabled,
        budget=budget,
    )
    return handler, provider
