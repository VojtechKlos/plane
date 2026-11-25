# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Publishes work item change events to Redis; apps/live forwards them to the browsers over a websocket.

Events carry ids only. Browsers fetch the changed data through the regular API, which enforces permissions.
The one exception is ``description_html`` on ``description_replaced`` events: apps/live needs it to merge
the new description into an open collaborative document, and strips it before forwarding.
"""

# Python imports
import json
import logging
import os
import uuid

# Django imports
from django.conf import settings
from django.utils import timezone

# Module imports
from plane.settings.redis import redis_instance

logger = logging.getLogger("plane.realtime")

CHANNEL = "plane:realtime:work-items"

# Marker the live description endpoint adds to issue_activity's requested_data so its own saves
# are not mistaken for an external description change. update_issue_activity ignores unknown keys.
LIVE_SOURCE_KEY = "realtime_source"
LIVE_SOURCE_VALUE = "live"

EVENT_CREATED = "work_item.created"
EVENT_UPDATED = "work_item.updated"
EVENT_DELETED = "work_item.deleted"
EVENT_COMMENT = "work_item.comment"

KIND_DESCRIPTION_REPLACED = "description_replaced"


def is_realtime_enabled():
    value = getattr(settings, "ENABLE_WORK_ITEM_REALTIME", None)
    if value is None:
        value = os.environ.get("ENABLE_WORK_ITEM_REALTIME", "1") == "1"
    return bool(value)


def event_type_for_activity(activity_type):
    """Map an issue_activity type to a realtime event type, or None when it is not a work item change."""
    if not activity_type:
        return None
    if activity_type.startswith("issue_draft.") or activity_type.startswith("issue_vote."):
        return None
    if activity_type == "issue.activity.created" or activity_type == "intake.activity.created":
        return EVENT_CREATED
    if activity_type == "issue.activity.deleted":
        return EVENT_DELETED
    if activity_type.startswith("comment.") or activity_type.startswith("comment_reaction."):
        return EVENT_COMMENT
    return EVENT_UPDATED


def publish_work_item_event(event_type, workspace_slug, project_id, issue_ids, actor_id=None, kind=None, extra=None):
    """Publish one event to Redis. Never raises: realtime is best effort and must not break the caller."""
    if not is_realtime_enabled():
        return False
    try:
        ids = [str(issue_id) for issue_id in (issue_ids or []) if issue_id]
        if not ids or not workspace_slug or not project_id:
            return False
        payload = {
            "id": uuid.uuid4().hex,
            "type": event_type,
            "kind": kind,
            "workspace_slug": str(workspace_slug),
            "project_id": str(project_id),
            "issue_ids": ids,
            "actor_id": str(actor_id) if actor_id else None,
            "ts": timezone.now().isoformat(),
        }
        if extra:
            payload.update(extra)
        redis_instance().publish(CHANNEL, json.dumps(payload))
        return True
    except Exception as e:  # noqa: BLE001 - realtime must never break the request or task
        logger.warning("Failed to publish realtime work item event: %s", e)
        return False


def _parse(data):
    if data is None:
        return {}
    if isinstance(data, dict):
        return data
    try:
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def publish_activity_event(activity_type, requested_data, issue_id, project, actor_id):
    """Hook called at the end of the issue_activity task, after the activity rows exist."""
    if not is_realtime_enabled() or issue_id is None:
        return
    event_type = event_type_for_activity(activity_type)
    if event_type is None:
        return
    try:
        workspace_slug = project.workspace.slug
        kind = activity_type
        extra = None
        data = _parse(requested_data)
        if (
            activity_type == "issue.activity.updated"
            and "description_html" in data
            and data.get(LIVE_SOURCE_KEY) != LIVE_SOURCE_VALUE
        ):
            # The description changed outside the collaborative editor (API, MCP, intake, ...).
            # apps/live merges the new html into a document that is open right now. The stored Y.js state is
            # kept on purpose: rebuilding it would start a new history that clients' local copies cannot merge
            # with; live reconciles it with the html on the next load instead.
            from plane.db.models import Issue

            issue = Issue.all_objects.filter(pk=issue_id).only("description_html").first()
            if issue is not None:
                kind = KIND_DESCRIPTION_REPLACED
                extra = {"description_html": issue.description_html or "<p></p>"}
        publish_work_item_event(event_type, workspace_slug, project.id, [issue_id], actor_id, kind, extra)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to publish realtime activity event: %s", e)


def publish_bulk_event(event_type, project_id, issue_ids, actor_id=None, kind=None):
    """For bulk changes that bypass issue_activity (bulk delete, automations, estimate changes)."""
    if not is_realtime_enabled():
        return
    try:
        from plane.db.models import Project

        project = Project.objects.select_related("workspace").filter(pk=project_id).first()
        if project is None:
            return
        publish_work_item_event(event_type, project.workspace.slug, project_id, list(issue_ids), actor_id, kind)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to publish realtime bulk event: %s", e)
