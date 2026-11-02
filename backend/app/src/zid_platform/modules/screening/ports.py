"""Public Protocol interface of the screening module. Other modules import ONLY this.

Lists:    `ingest_list` loads a sanctions list file (refused whole if it is malformed or truncated).
Cases:    `open_case`, `amend_case`, `rescreen`, `request_rescreen`, `request_appeal`, `review`, `replay`, `due_for_rescreen`, `get_case` (the safe view),
          `restricted_detail` (alerts with listed names: only for a designated compliance reviewer).
Decision: a case is VERIFIED only with every required list current, no alert, a resolved ownership structure, a country
          pack, and proven authority; anything else waits for a person.
"""

from __future__ import annotations

from zid_platform.modules.screening.application.passes import ingest_pass, rescreen_pass
from zid_platform.modules.screening.application.screening import (
    CaseView,
    amend_case,
    case_for_kyb,
    due_for_rescreen,
    get_case,
    history,
    ingest_list,
    live_kyb_cases,
    open_case,
    record_register,
    replay,
    request_appeal,
    request_rescreen,
    rescreen,
    restricted_detail,
    review,
)
from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    RegisterEvidence,
    RegisterPerson,
    Role,
    Ruling,
    ScreeningError,
    ScreeningStatus,
)
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.domain.ownership import Control, Edge, Graph, Holder, HolderKind
from zid_platform.modules.screening.infrastructure.config import config as screening_config

__all__ = [
    "AuthorityEvidence",
    "AuthorityKind",
    "CaseView",
    "Control",
    "Edge",
    "Graph",
    "Holder",
    "HolderKind",
    "ListId",
    "Party",
    "PartyKind",
    "RegisterEvidence",
    "RegisterPerson",
    "Role",
    "Ruling",
    "ScreeningError",
    "ScreeningStatus",
    "amend_case",
    "case_for_kyb",
    "due_for_rescreen",
    "get_case",
    "history",
    "ingest_list",
    "ingest_pass",
    "live_kyb_cases",
    "open_case",
    "record_register",
    "replay",
    "request_appeal",
    "request_rescreen",
    "rescreen",
    "rescreen_pass",
    "restricted_detail",
    "review",
    "screening_config",
]
