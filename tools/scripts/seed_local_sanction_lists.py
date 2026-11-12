"""Load small SYNTHETIC sanctions lists on a LOCAL cell (development only; every name here is fictional).

Owner screening answers UNAVAILABLE, never "clear", until the five required lists (OFAC SDN, OFAC non-SDN, UK, EU, UN) are loaded and no older than
`list_max_age_hours` (48), and the real downloads are not available on a development machine (the EU list needs a registered account). So company
verification cannot get past screening locally. This loads one tiny file per list, in each list's real file format, through the application's own
`ingest_list` (the same parsers and checks as production), with a fictional designated party that matches no one a tester would enter.

    python tools/scripts/seed_local_sanction_lists.py

Run it again to keep the lists fresh (the same file only moves its fetch time). Refuses to run unless ZID_ENVIRONMENT is local or test and the
database is on this machine.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

from bootstrap_local import cell_db_url
from localenv import BACKEND_ENV, load_backend_env

# One designated party per list, fictional and unlike any name used in the tests or the walk-through ("Jane Mary Doe", "Alex Smith").
SDN = b'9001,"ZZTESTLIST, Placeholder","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 1901"\n'
UK = b"Unique ID,Name 1,Name 6,Name type\nUK9001,Placeholder,Zztestlist,Primary Name\n"
EU = (
    b'<export><sanctionEntity logicalId="9001"><subjectType code="person"/>'
    b'<nameAlias wholeName="Placeholder Zztestlist"/></sanctionEntity></export>'
)
UN = (
    b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL><DATAID>9001</DATAID><FIRST_NAME>Placeholder</FIRST_NAME>"
    b"<SECOND_NAME>Zztestlist</SECOND_NAME><UN_LIST_TYPE>T</UN_LIST_TYPE></INDIVIDUAL></INDIVIDUALS><ENTITIES/></CONSOLIDATED_LIST>"
)


async def load(url: str) -> list[tuple[str, str]]:
    from sqlalchemy.ext.asyncio import create_async_engine
    from zid_platform.modules.screening.application import screening as service
    from zid_platform.modules.screening.domain.lists import ListId

    now = datetime.now(UTC)
    engine = create_async_engine(url)
    done: list[tuple[str, str]] = []
    try:
        async with engine.begin() as c:
            for list_id, files in (
                (ListId.OFAC_SDN, {"sdn": SDN}),
                (ListId.OFAC_NON_SDN, {"sdn": SDN}),
                (ListId.UK_SANCTIONS, {"file": UK}),
                (ListId.EU_CONSOLIDATED, {"file": EU}),
                (ListId.UN_SC, {"file": UN}),
            ):
                result = await service.ingest_list(
                    c,
                    list_id,
                    files,
                    fetched_at=now,
                    source_ref="local-development-synthetic",
                    now=now,
                )
                done.append((list_id.value, result))
    finally:
        await engine.dispose()
    return done


def main() -> int:
    if not load_backend_env():
        print(f"{BACKEND_ENV} not found: copy backend/.env.example to backend/.env first.")
        return 1
    if os.environ.get("ZID_ENVIRONMENT", "local") not in ("local", "test"):
        print("seed_local_sanction_lists only runs with ZID_ENVIRONMENT=local or test.")
        return 1
    url = cell_db_url()
    if "localhost" not in url and "127.0.0.1" not in url:
        print("Refusing to change a database that is not on this machine.")
        return 1
    for list_id, result in asyncio.run(load(url)):
        print(f"{list_id:<16} {result}")
    print("synthetic sanctions lists are loaded and fresh for 48 hours")
    return 0


if __name__ == "__main__":
    sys.exit(main())
