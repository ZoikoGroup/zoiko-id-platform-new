"""Flip a feature flag or the kill switch at runtime, with no deploy (ADR-14, OD-021).

    python -m zid_assistant.flags_cli show
    python -m zid_assistant.flags_cli set kill_switch on      # emergency stop: chat, model and intake all stop at once
    python -m zid_assistant.flags_cli set ai_answers off      # stop only the model; verified source excerpts keep working
    python -m zid_assistant.flags_cli clear ai_answers        # back to the configured default

Withdraw published content at once on every replica (no deploy; checked at serving time and at link click time):
    python -m zid_assistant.flags_cli revoke source PUB-005
    python -m zid_assistant.flags_cli revoke link OVERVIEW
    python -m zid_assistant.flags_cli revoke capability CAP-001
    python -m zid_assistant.flags_cli restore source PUB-005
    python -m zid_assistant.flags_cli revoked

Needs ZID_ASSISTANT_VALKEY_URL: the override is shared by every replica within a few seconds.
"""

from __future__ import annotations

import asyncio
import sys

from zid_assistant.infrastructure.flags import FLAG_NAMES, FlagStore
from zid_assistant.infrastructure.revocations import KINDS, RevocationStore
from zid_assistant.settings import AssistantSettings


async def _run(argv: list[str]) -> int:
    s = AssistantSettings()
    if not s.valkey_url:
        print(
            "ZID_ASSISTANT_VALKEY_URL is not set: runtime flags need the shared store",
            file=sys.stderr,
        )
        return 2
    store = FlagStore(s, ttl=0)
    revs = RevocationStore(s, ttl=0)
    cmd = argv[0] if argv else "show"
    if cmd == "revoked":
        print(await revs.get())
    elif cmd in ("revoke", "restore") and len(argv) == 3 and argv[1] in KINDS:
        await (revs.revoke if cmd == "revoke" else revs.restore)(argv[1], argv[2])
        print(await revs.get())
    elif cmd == "show":
        print(await store.get())
    elif cmd == "set" and len(argv) == 3 and argv[1] in FLAG_NAMES and argv[2] in ("on", "off"):
        await store.set(argv[1], argv[2] == "on")
        print(await store.get())
    elif cmd == "clear" and len(argv) == 2 and argv[1] in FLAG_NAMES:
        await store.clear(argv[1])
        print(await store.get())
    else:
        print(__doc__)
        return 2
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run(sys.argv[1:])))


if __name__ == "__main__":
    main()
