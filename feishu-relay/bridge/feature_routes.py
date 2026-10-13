"""HTTP routes supplied by the group-automation bridge features."""

from __future__ import annotations

from chat_members import ROUTES as CHAT_MEMBER_ROUTES
from join_link import ROUTES as JOIN_LINK_ROUTES
from target_groups import ROUTES as TARGET_GROUP_ROUTES
from target_observer import ROUTES as TARGET_OBSERVER_ROUTES
from target_registry import ROUTES as TARGET_REGISTRY_ROUTES
from urgent_alerts import ROUTES as URGENT_ALERT_ROUTES


FEATURE_ROUTES = {
	**TARGET_REGISTRY_ROUTES,
	**JOIN_LINK_ROUTES,
	**TARGET_GROUP_ROUTES,
	**CHAT_MEMBER_ROUTES,
	**URGENT_ALERT_ROUTES,
	**TARGET_OBSERVER_ROUTES,
}
