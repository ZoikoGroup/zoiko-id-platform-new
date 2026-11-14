"""Public Protocol interface of the screening module. Other modules import ONLY this.

Lists:    `ingest_list` loads a sanctions list file (refused whole if it is malformed or truncated).
Cases:    `open_case`, `amend_case`, `rescreen`, `request_rescreen`, `request_appeal`, `review`, `replay`, `due_for_rescreen`, `get_case` (the safe view),
          `restricted_detail` (alerts with listed names: only for a designated compliance reviewer).
Decision: a case is VERIFIED only with every required list current, no alert, a resolved ownership structure, a country
          pack, and proven authority; anything else waits for a person.
"""

from __future__ import annotations

from zid_platform.modules.screening.application.monitoring_gate import (
    MonitoringNotApproved,
    monitoring_allowed,
    require_monitoring,
)
from zid_platform.modules.screening.application.monitoring_gate import (
    configure as configure_monitoring_scope,
)
from zid_platform.modules.screening.application.passes import ingest_pass, rescreen_pass
from zid_platform.modules.screening.application.screening import (
    CaseView,
    amend_case,
    case_for_kyb,
    close_for_kyb,
    configure_usage_sink,
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
from zid_platform.modules.screening.application.signals import (
    EVENT as SIGNAL_EVENT_CHANNEL,
)
from zid_platform.modules.screening.application.signals import (
    AdverseMediaPort,
    check_adverse_media,
    note_attempt,
    prune_velocity,
    record_attempt,
    retry_failed_notifications,
    review_view,
)
from zid_platform.modules.screening.application.signals import challenge as challenge_signal
from zid_platform.modules.screening.application.signals import open_queue as open_signal_queue
from zid_platform.modules.screening.application.signals import resolve as resolve_signal
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
from zid_platform.modules.screening.domain.matching import Party, name_score
from zid_platform.modules.screening.domain.monitoring_scope import Kind as MonitoringKind
from zid_platform.modules.screening.domain.ownership import Control, Edge, Graph, Holder, HolderKind
from zid_platform.modules.screening.domain.signals import SIGNAL_OUTCOMES
from zid_platform.modules.screening.domain.signals import Action as AttemptAction
from zid_platform.modules.screening.infrastructure.config import config as screening_config

__all__ = [
    "SIGNAL_EVENT_CHANNEL",
    "SIGNAL_OUTCOMES",
    "AdverseMediaPort",
    "AttemptAction",
    "AuthorityEvidence",
    "AuthorityKind",
    "CaseView",
    "Control",
    "Edge",
    "Graph",
    "Holder",
    "HolderKind",
    "ListId",
    "MonitoringKind",
    "MonitoringNotApproved",
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
    "challenge_signal",
    "check_adverse_media",
    "close_for_kyb",
    "configure_monitoring_scope",
    "configure_usage_sink",
    "due_for_rescreen",
    "get_case",
    "history",
    "ingest_list",
    "ingest_pass",
    "live_kyb_cases",
    "monitoring_allowed",
    "name_score",
    "note_attempt",
    "open_case",
    "open_signal_queue",
    "prune_velocity",
    "record_attempt",
    "record_register",
    "replay",
    "request_appeal",
    "request_rescreen",
    "require_monitoring",
    "rescreen",
    "rescreen_pass",
    "resolve_signal",
    "restricted_detail",
    "retry_failed_notifications",
    "review",
    "review_view",
    "screening_config",
]
