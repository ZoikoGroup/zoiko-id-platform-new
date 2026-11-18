"""Biometric method states and proposals in PostgreSQL (ZID-DTV-WF-04 BIO-03; CR-107). Platform configuration: not tenant data, no RLS.

The domain rules live in `domain/biometric_methods.py`; the tables repeat the ones that must hold even for a caller that skips the domain code
(migrations verification_0019, verification_0020). The SQL is shared with the WF-05 check methods (`method_store.MethodTables`, CR-112); this module
binds it to the biometric tables and keeps the functions its callers use.
"""

from __future__ import annotations

from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.infrastructure.method_store import MethodTables

TABLES = MethodTables(
    "verify.biometric_method_state", "verify.biometric_method_proposal", bm.Method
)

history = TABLES.history
scope_id_of = TABLES.scope_id_of
for_call = TABLES.for_call
all_states = TABLES.all_states
proposals = TABLES.proposals
proposal = TABLES.proposal
insert_proposal = TABLES.insert_proposal
decide_proposal = TABLES.decide_proposal
insert_state = TABLES.insert_state

__all__ = [
    "TABLES",
    "all_states",
    "decide_proposal",
    "for_call",
    "history",
    "insert_proposal",
    "insert_state",
    "proposal",
    "proposals",
    "scope_id_of",
]
