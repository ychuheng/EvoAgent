"""Repository 共用的错误类型。"""


class RecordNotFoundError(LookupError):
    """请求的持久化记录不存在。"""


class ConcurrentUpdateError(RuntimeError):
    """记录已经被其他事务修改，当前写入不能继续。"""
