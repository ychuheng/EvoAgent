"""Host-selected generation identity, with no credential values in the snapshot."""

from evoagent.skills.canonical import content_hash


def candidate_configuration(settings):
    return {
        "provider": settings.provider.value,
        "model": settings.skill_extractor_model
        or ("mock" if settings.provider.value == "mock" else None),
        "max_output_tokens": min(settings.model_request_max_output_tokens or 4096, 4096),
        "schema_version": 2,
        "max_steps": settings.skill_max_steps,
        "max_risk": settings.skill_max_effective_risk.value,
        "allowed_tools": list(settings.skill_allowed_tools),
        "endpoint_identity_hash": content_hash(str(settings.base_url))
        if settings.base_url
        else None,
        "generator_version": "personal:v2",
        "budget_scope": settings.budget_scope,
        "input_price_micros_per_million": settings.budget_input_price_micros_per_million,
        "output_price_micros_per_million": settings.budget_output_price_micros_per_million,
    }
