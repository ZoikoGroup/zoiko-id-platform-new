"""The step-up guard for modules that sit below authentication (ZID-ENG-DEC-009).

Authentication imports evidence, session and others, so those cannot import it back without a dependency cycle. The guard is composed in
at start-up (`app.state.step_up_guard`, set in `main`) and a route calls `composed_step_up` instead. A cell built without it, such as a
test of one module alone, has no step-up; a deployed cell is always built through `create_app`, which sets it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from zid_platform.api_auth import Caller


async def composed_step_up(request: Request, conn: Any, caller: Caller, **command: Any) -> None:
    guard = getattr(request.app.state, "step_up_guard", None)
    if guard is not None:
        await guard(request, conn, caller, **command)
