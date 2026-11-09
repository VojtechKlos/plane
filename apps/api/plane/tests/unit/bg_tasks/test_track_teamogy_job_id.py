# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""A change of the Teamogy job ID is recorded as one work item activity."""

from uuid import uuid4

import pytest

from plane.bgtasks.issue_activities_task import track_teamogy_job_id

pytestmark = pytest.mark.unit


def _track(current, requested):
    activities = []
    track_teamogy_job_id(
        requested_data=requested,
        current_instance=current,
        issue_id=uuid4(),
        project_id=uuid4(),
        workspace_id=uuid4(),
        actor_id=uuid4(),
        issue_activities=activities,
        epoch=0,
    )
    return activities


def test_records_a_changed_job_id():
    [activity] = _track({"teamogy_job_id": "J-1"}, {"teamogy_job_id": "J-2"})

    assert activity.field == "teamogy_job_id"
    assert activity.verb == "updated"
    assert activity.old_value == "J-1"
    assert activity.new_value == "J-2"


def test_records_a_first_job_id_with_empty_old_value():
    [activity] = _track({"teamogy_job_id": None}, {"teamogy_job_id": "J-2"})

    assert activity.old_value == ""
    assert activity.new_value == "J-2"


def test_records_a_cleared_job_id_with_empty_new_value():
    [activity] = _track({"teamogy_job_id": "J-1"}, {"teamogy_job_id": ""})

    assert activity.old_value == "J-1"
    assert activity.new_value == ""


@pytest.mark.parametrize(
    "current, requested",
    [
        (None, ""),
        (None, "   "),
        ("J-1", " J-1 "),
    ],
)
def test_ignores_an_unchanged_job_id(current, requested):
    assert _track({"teamogy_job_id": current}, {"teamogy_job_id": requested}) == []
