"""Learning calls obey both global and reserved personal sub-budgets."""

from evoagent.core.models import ProviderEventType
from evoagent.learning.schema import LearningError
from evoagent.runtime.budget import BudgetScope, cost_micros, limits_from_settings
from evoagent.sandbox.cancellation import finish_on_cancel
from evoagent.skills.canonical import canonical_json, content_hash


class LearningBudgetedProvider:
    def __init__(self, provider, budget, request_id, guard, *, check, provider_name):
        self.provider, self.budget = provider, budget
        self.request_id, self.guard = request_id, guard
        self.check, self.provider_name = check, provider_name

    async def stream(self, request):
        if request.max_output_tokens is None:
            raise LearningError("learning_output_bound_required")
        limits = limits_from_settings(
            self.budget.settings, BudgetScope(self.budget.settings.budget_scope)
        )
        if not limits.priced:
            raise LearningError("learning_price_unknown")
        body = request.model_dump(mode="json")
        # UTF-8 bytes + a bounded envelope overhead conservatively bound input;
        # this is a reservation estimate, not fabricated provider usage.
        input_bound = len(canonical_json(body).encode()) + 1024
        maximum = cost_micros(
            limits, input_tokens=input_bound, output_tokens=request.max_output_tokens
        )
        await self.budget.reconcile_stale(limit=100)
        await self.check()
        call_key = f"learning:{self.request_id}:{self.guard.job_id}:{content_hash(body)}"
        identity = await self.budget.reserve(self.request_id, call_key, maximum, guard=self.guard)
        sent, usage = False, None
        try:
            await self.check()
            await self.budget.mark_dispatched(identity, guard=self.guard)
            sent = True
            await self.check()  # cancellation after send intent is conservative unknown
            async for event in self.provider.stream(request):
                observed = event.usage if event.type is ProviderEventType.USAGE else None
                if event.type is ProviderEventType.COMPLETED and event.response:
                    observed = event.response.usage or observed
                if observed is not None:
                    if usage is not None and usage != observed:
                        usage = None
                        raise LearningError("learning_usage_conflict")
                    usage = observed
                yield event
        finally:
            if sent:
                await finish_on_cancel(
                    self.budget.settle(
                        identity, usage, provider=self.provider_name, model=request.model
                    )
                )
            else:
                await finish_on_cancel(self.budget.release_before_dispatch(identity))
