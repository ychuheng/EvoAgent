"""公网工具出口：每跳检查全部 DNS 结果，并绑定实际 IP、Host 和 TLS SNI。"""

import httpx

from evoagent.tools.base import ToolExecutionError
from evoagent.tools.guards import URLGuard


class EgressTransport(httpx.AsyncBaseTransport):
    def __init__(self, guard=None, inner=None):
        self.guard = guard or URLGuard()
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        _, addresses = await self.guard.resolve(str(request.url))
        host = request.url.host
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        headers["Accept-Encoding"] = "identity"
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = host.encode("ascii")
        bound = httpx.Request(
            request.method,
            request.url.copy_with(host=sorted(addresses)[0]),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = await self.inner.handle_async_request(bound)
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise ToolExecutionError("compressed web responses are not supported")
        return response

    async def aclose(self):
        await self.inner.aclose()
