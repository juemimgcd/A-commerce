"""业务错误只公开稳定代码，不把 SQL、凭证或外部响应体暴露给客户端。"""


class CommerceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 409,
        retryable: bool = False,
        *,
        retry_after: int | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(message)


def missing() -> CommerceError:
    return CommerceError("NOT_FOUND", "资源不存在或不属于当前账号。", 404)
