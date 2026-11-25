# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Realtime publisher: which activities become which events, and external description changes."""

import json
from unittest import mock

import pytest

from plane.db.models import Issue, Project, ProjectMember
from plane.realtime import publisher
from plane.utils.issue_filters import issue_filters

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "activity_type,expected",
    [
        ("issue.activity.created", "work_item.created"),
        ("intake.activity.created", "work_item.created"),
        ("issue.activity.updated", "work_item.updated"),
        ("issue.activity.deleted", "work_item.deleted"),
        ("cycle.activity.created", "work_item.updated"),
        ("module.activity.deleted", "work_item.updated"),
        ("link.activity.created", "work_item.updated"),
        ("attachment.activity.deleted", "work_item.updated"),
        ("issue_relation.activity.created", "work_item.updated"),
        ("comment.activity.created", "work_item.comment"),
        ("comment.activity.deleted", "work_item.comment"),
        ("comment_reaction.activity.created", "work_item.comment"),
        ("issue_draft.activity.created", None),
        ("issue_vote.activity.created", None),
        (None, None),
    ],
)
def test_event_type_for_activity(activity_type, expected):
    assert publisher.event_type_for_activity(activity_type) == expected


def test_publish_sends_json_to_the_channel():
    redis = mock.Mock()
    with mock.patch.object(publisher, "redis_instance", return_value=redis):
        assert publisher.publish_work_item_event("work_item.updated", "ws", "p1", ["i1", None], "u1", "kind") is True

    channel, raw = redis.publish.call_args.args
    payload = json.loads(raw)
    assert channel == publisher.CHANNEL
    assert payload["type"] == "work_item.updated"
    assert payload["workspace_slug"] == "ws"
    assert payload["project_id"] == "p1"
    assert payload["issue_ids"] == ["i1"]
    assert payload["actor_id"] == "u1"
    assert payload["kind"] == "kind"
    assert payload["id"]


def test_publish_never_raises():
    with mock.patch.object(publisher, "redis_instance", side_effect=ConnectionError("down")):
        assert publisher.publish_work_item_event("work_item.updated", "ws", "p1", ["i1"]) is False


def test_publish_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_WORK_ITEM_REALTIME", "0")
    redis = mock.Mock()
    with mock.patch.object(publisher, "redis_instance", return_value=redis):
        assert publisher.publish_work_item_event("work_item.updated", "ws", "p1", ["i1"]) is False
    redis.publish.assert_not_called()


def test_issue_ids_filter_keeps_only_valid_uuids():
    valid = "6f1b8a8e-3c3c-4d1e-9a51-1d6f5c2a7b10"
    filters = issue_filters({"issue_ids": f"{valid},nope"}, "GET")
    assert [str(i) for i in filters["id__in"]] == [valid]
    assert issue_filters({"issue_ids": "nope"}, "GET")["id__in"] == []


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Realtime", identifier="RTP", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
    return project


def _issue(workspace, project, user, **kwargs):
    issue = Issue(name="Work item", workspace=workspace, project=project, **kwargs)
    issue.save(created_by_id=user.id)
    return issue


@pytest.mark.django_db
def test_external_description_change_keeps_binary_and_sends_html(workspace, project, create_user):
    issue = _issue(workspace, project, create_user, description_html="<p>external</p>", description_binary=b"\x01\x02")
    with mock.patch.object(publisher, "publish_work_item_event") as publish:
        publisher.publish_activity_event(
            "issue.activity.updated", json.dumps({"description_html": "<p>external</p>"}), issue.id, project, "u1"
        )

    issue.refresh_from_db()
    assert bytes(issue.description_binary) == b""
    args = publish.call_args.args
    assert args[0] == "work_item.updated"
    assert args[1] == workspace.slug
    assert args[5] == publisher.KIND_DESCRIPTION_REPLACED
    assert args[6] == {"description_html": "<p>external</p>"}


@pytest.mark.django_db
def test_live_description_save_keeps_binary(workspace, project, create_user):
    issue = _issue(workspace, project, create_user, description_binary=b"\x01\x02")
    data = json.dumps({"description_html": "<p>x</p>", publisher.LIVE_SOURCE_KEY: publisher.LIVE_SOURCE_VALUE})
    with mock.patch.object(publisher, "publish_work_item_event") as publish:
        publisher.publish_activity_event("issue.activity.updated", data, issue.id, project, "u1")

    issue.refresh_from_db()
    assert bytes(issue.description_binary) == b"\x01\x02"
    assert publish.call_args.args[5] == "issue.activity.updated"
    assert publish.call_args.args[6] is None


@pytest.mark.django_db
def test_property_update_publishes_plain_update(workspace, project, create_user):
    issue = _issue(workspace, project, create_user, description_binary=b"\x01\x02")
    with mock.patch.object(publisher, "publish_work_item_event") as publish:
        publisher.publish_activity_event(
            "issue.activity.updated", json.dumps({"priority": "high"}), issue.id, project, "u1"
        )

    issue.refresh_from_db()
    assert bytes(issue.description_binary) == b"\x01\x02"
    assert publish.call_args.args[0] == "work_item.updated"
    assert publish.call_args.args[4] == "u1"
