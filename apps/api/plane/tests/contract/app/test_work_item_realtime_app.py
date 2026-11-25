# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Realtime work item endpoints: project membership for apps/live, the collaborative description and issue_ids."""

import base64
import json
from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember, User, WorkspaceMember

pytestmark = pytest.mark.contract

BINARY = base64.b64encode(b"\x01\x02\x03\x04\x05\x06").decode()


@pytest.fixture
def tasks():
    with (
        mock.patch("plane.realtime.views.issue_activity") as issue_activity,
        mock.patch("plane.realtime.views.model_activity"),
        mock.patch("plane.realtime.views.issue_description_version_task") as version_task,
        mock.patch("plane.realtime.views.base_host", return_value="http://testserver"),
    ):
        yield {"issue_activity": issue_activity, "version_task": version_task}


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Realtime", identifier="RT", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
    return project


@pytest.fixture
def client(create_user):
    client = APIClient()
    client.force_authenticate(user=create_user)
    return client


@pytest.fixture
def other_user(db, workspace):
    user = User.objects.create(email="other@plane.so", username="other-user", first_name="Other", last_name="User")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    return user


def _issue(workspace, project, user, **kwargs):
    issue = Issue(name="Work item", workspace=workspace, project=project, **kwargs)
    issue.save(created_by_id=user.id)
    return issue


def _description_url(workspace, project, issue):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/realtime-description/"


@pytest.mark.django_db
def test_realtime_projects_lists_only_active_memberships(client, workspace, project, create_user):
    hidden = Project.objects.create(name="Hidden", identifier="HID", workspace=workspace, created_by=create_user)
    left = Project.objects.create(name="Left", identifier="LFT", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(workspace=workspace, project=left, member=create_user, role=15, is_active=False)

    response = client.get(f"/api/workspaces/{workspace.slug}/realtime/projects/")

    assert response.status_code == status.HTTP_200_OK
    assert response.data["project_ids"] == [str(project.id)]
    assert str(hidden.id) not in response.data["project_ids"]


@pytest.mark.django_db
def test_realtime_projects_requires_authentication(workspace):
    response = APIClient().get(f"/api/workspaces/{workspace.slug}/realtime/projects/")
    assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@pytest.mark.django_db
def test_get_description_returns_binary(client, workspace, project, create_user):
    issue = _issue(
        workspace, project, create_user, description_binary=b"\x09\x08\x07\x06", description_html="<p>hi</p>"
    )

    response = client.get(_description_url(workspace, project, issue))

    assert response.status_code == status.HTTP_200_OK
    assert base64.b64decode(response.data["description_binary"]) == b"\x09\x08\x07\x06"
    assert response.data["description_html"] == "<p>hi</p>"
    assert response.data["can_edit"] is True


@pytest.mark.django_db
def test_get_description_empty_when_no_binary(client, workspace, project, create_user):
    issue = _issue(workspace, project, create_user)

    response = client.get(_description_url(workspace, project, issue))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["description_binary"] is None


@pytest.mark.django_db
def test_get_description_read_only_for_guest_who_may_see_all(workspace, project, create_user, other_user):
    project.guest_view_all_features = True
    project.save()
    ProjectMember.objects.create(workspace=workspace, project=project, member=other_user, role=5)
    issue = _issue(workspace, project, create_user)
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.get(_description_url(workspace, project, issue))

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["can_edit"] is False


@pytest.mark.django_db
def test_get_description_forbidden_for_non_member(workspace, project, create_user, other_user):
    issue = _issue(workspace, project, create_user)
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.get(_description_url(workspace, project, issue))

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_patch_description_saves_and_logs_activity_marked_as_live(client, workspace, project, create_user, tasks):
    issue = _issue(workspace, project, create_user, description_html="<p>old</p>")

    response = client.patch(
        _description_url(workspace, project, issue),
        {"description_binary": BINARY, "description_html": "<p>new</p>", "description_json": {"type": "doc"}},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    issue.refresh_from_db()
    assert bytes(issue.description_binary) == base64.b64decode(BINARY)
    assert issue.description_html == "<p>new</p>"
    assert issue.description_stripped == "new"
    kwargs = tasks["issue_activity"].delay.call_args.kwargs
    requested = json.loads(kwargs["requested_data"])
    assert requested["description_html"] == "<p>new</p>"
    assert requested["realtime_source"] == "live"
    assert json.loads(kwargs["current_instance"])["description_html"] == "<p>old</p>"
    tasks["version_task"].delay.assert_called_once()


@pytest.mark.django_db
def test_patch_description_without_html_change_only_stores_binary(client, workspace, project, create_user, tasks):
    issue = _issue(workspace, project, create_user, description_html="<p>same</p>")

    response = client.patch(
        _description_url(workspace, project, issue),
        {"description_binary": BINARY, "description_html": "<p>same</p>"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    issue.refresh_from_db()
    assert bytes(issue.description_binary) == base64.b64decode(BINARY)
    tasks["issue_activity"].delay.assert_not_called()


@pytest.mark.django_db
def test_patch_description_rejects_invalid_binary(client, workspace, project, create_user, tasks):
    issue = _issue(workspace, project, create_user)
    bad = base64.b64encode(b"<script>alert(1)</script>").decode()

    response = client.patch(
        _description_url(workspace, project, issue),
        {"description_binary": bad, "description_html": "<p>x</p>"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_patch_description_forbidden_for_guest_of_others_issue(workspace, project, create_user, other_user, tasks):
    ProjectMember.objects.create(workspace=workspace, project=project, member=other_user, role=5)
    issue = _issue(workspace, project, create_user)
    client = APIClient()
    client.force_authenticate(user=other_user)

    response = client.patch(
        _description_url(workspace, project, issue),
        {"description_binary": BINARY, "description_html": "<p>x</p>"},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_issue_ids_narrows_the_issue_list(client, workspace, project, create_user):
    first = _issue(workspace, project, create_user)
    _issue(workspace, project, create_user)

    response = client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/?issue_ids={first.id}")

    assert response.status_code == status.HTTP_200_OK, response.data
    assert [str(row["id"]) for row in response.data["results"]] == [str(first.id)]


@pytest.mark.django_db
def test_issue_ids_with_only_invalid_ids_matches_nothing(client, workspace, project, create_user):
    _issue(workspace, project, create_user)

    response = client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/?issue_ids=not-a-uuid")

    assert response.status_code == status.HTTP_200_OK, response.data
    assert list(response.data["results"]) == []
