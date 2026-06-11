"""Strict request handling — fail loudly instead of silently ignoring input.

FastAPI silently drops query parameters it doesn't recognize. That hides client
mistakes (typos, unsupported options) until they surface as confusing "why is my
filter doing nothing?" bugs much later. Routes built with ``StrictQueryParamsRoute``
reject any undeclared query parameter with a ``400`` so the client is told at once.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def _allowed_query_params(dependant) -> set[str]:
    """Every query-parameter name (alias) the route accepts, recursively
    including those contributed by its dependencies."""
    names = {field.alias for field in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _allowed_query_params(sub)
    return names


class StrictQueryParamsRoute(APIRoute):
    """APIRoute that returns 400 for any query parameter the endpoint doesn't declare."""

    def get_route_handler(self) -> Callable:
        original_handler = super().get_route_handler()
        allowed = _allowed_query_params(self.dependant)

        async def handler(request: Request) -> Response:
            unknown = sorted({k for k in request.query_params if k not in allowed})
            if unknown:
                return JSONResponse(
                    status_code=400,
                    content={
                        "ok": False,
                        "error": {
                            "code": "UNKNOWN_QUERY_PARAM",
                            "message": (
                                f"Unknown query parameter(s): {', '.join(unknown)}. "
                                f"Allowed: {', '.join(sorted(allowed)) or '(none)'}."
                            ),
                        },
                    },
                )
            return await original_handler(request)

        return handler
