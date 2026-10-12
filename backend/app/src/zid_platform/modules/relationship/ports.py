"""Public interface of the relationship module. Other modules import ONLY this."""

from zid_platform.modules.relationship.infrastructure.edge_store import current_revision

__all__ = ["current_revision"]
