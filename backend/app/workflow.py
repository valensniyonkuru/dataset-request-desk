"""The request status workflow, as plain data.

TRANSITIONS is the only place the rules live: which status changes exist and
which roles may make each one. The API and available_transitions() both read
it; no other code hard-codes a rule.

    submitted -> in_progress -> delivered -> accepted
                                          -> rejected -> in_progress (rework)
"""

from typing import Literal

Status = Literal["submitted", "in_progress", "delivered", "accepted", "rejected"]

INITIAL_STATUS: Status = "submitted"

# Episodes can only be assigned to or removed from a request in this status.
# Before it, work has not started; after it, the delivery is with the client and
# must not change under their eyes (and the delivery gate could be bypassed).
ASSIGNMENTS_OPEN_STATUS: Status = "in_progress"

# (from_status, to_status) -> roles allowed to make that change.
# Accepting or rejecting a delivery is the client's decision, so admin is not listed there.
TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    ("submitted", "in_progress"): frozenset({"operator", "admin"}),
    ("in_progress", "delivered"): frozenset({"operator", "admin"}),
    ("delivered", "accepted"): frozenset({"client"}),
    ("delivered", "rejected"): frozenset({"client"}),
    ("rejected", "in_progress"): frozenset({"operator", "admin"}),
}


def available_transitions(status: str, role: str) -> list[str]:
    """The statuses a user with this role may move a request to, from `status`."""
    return [
        to_status
        for (from_status, to_status), roles in TRANSITIONS.items()
        if from_status == status and role in roles
    ]
