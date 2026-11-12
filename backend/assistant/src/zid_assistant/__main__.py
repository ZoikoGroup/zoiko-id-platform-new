"""Run the service:  python -m zid_assistant   (port ZID_ASSISTANT_PORT, default 8210)."""

from __future__ import annotations

import uvicorn

from zid_assistant.settings import AssistantSettings


def main() -> None:
    s = AssistantSettings()
    uvicorn.run(
        "zid_assistant.main:create_app",
        factory=True,
        host=s.bind_host,
        port=s.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
