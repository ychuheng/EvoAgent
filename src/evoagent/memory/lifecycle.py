"""状态转换只管理可信度；模型输出永远不能确认自己。"""

from evoagent.memory.schema import MemoryError


def next_status(current: str, action: str) -> str:
    allowed = {
        "confirm": ({"proposed"}, "confirmed"),
        "reject": ({"proposed"}, "rejected"),
        "revoke": ({"proposed", "confirmed"}, "revoked"),
        "erase": ({"proposed", "confirmed", "rejected", "revoked", "superseded"}, "revoked"),
    }
    sources, target = allowed[action]
    if current not in sources:
        raise MemoryError("invalid_memory_transition")
    return target
