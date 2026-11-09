# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Work items carry an optional Teamogy job ID that the app API saves and returns."""

import json
from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Issue, Project, ProjectMember

pytestmark = pytest.mark.contract


@pytest.fixture
def tasks():
    """Stub the deferred tasks (broker) and base_host (needs APP_BASE_URL / WEB_URL)."""
    with (
        mock.patch("plane.app.views.issue.base.issue_activity") as issue_activity,
        mock.patch("plane.app.views.issue.base.model_activity"),
        mock.patch("plane.app.views.issue.base.issue_description_version_task"),
        mock.patch("plane.app.views.issue.base.recent_visited_task"),
        mock.patch("plane.app.views.issue.base.base_host", return_value="http://testserver"),
    ):
        yield issue_activity


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Teamogy", identifier="TMG", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
    return project


@pytest.fixture
def client(create_user):
    client = APIClient()
    client.force_authenticate(user=create_user)
    return client


def _issues_url(workspace, project):
    return f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/"


def _create_issue(workspace, project, user, **kwargs):
    issue = Issue(name="Work item", workspace=workspace, project=project, **kwargs)
    issue.save(created_by_id=user.id)
    return issue


@pytest.mark.django_db
def test_create_saves_and_returns_trimmed_job_id(client, workspace, project, tasks):
    response = client.post(
        _issues_url(workspace, project), {"name": "With job", "teamogy_job_id": "  J-1042 "}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["teamogy_job_id"] == "J-1042"
    assert Issue.objects.get(pk=response.data["id"]).teamogy_job_id == "J-1042"


@pytest.mark.django_db
def test_create_without_job_id_leaves_it_empty(client, workspace, project, tasks):
    response = client.post(_issues_url(workspace, project), {"name": "Without job"}, format="json")

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["teamogy_job_id"] is None
    assert Issue.objects.get(pk=response.data["id"]).teamogy_job_id is None


@pytest.mark.django_db
def test_patch_changes_job_id_and_reports_it(client, workspace, project, create_user, tasks):
    issue = _create_issue(workspace, project, create_user, teamogy_job_id="J-1")

    response = client.patch(f"{_issues_url(workspace, project)}{issue.id}/", {"teamogy_job_id": "J-2"}, format="json")

    assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
    issue.refresh_from_db()
    assert issue.teamogy_job_id == "J-2"
    kwargs = tasks.delay.call_args.kwargs
    assert json.loads(kwargs["requested_data"])["teamogy_job_id"] == "J-2"
    assert json.loads(kwargs["current_instance"])["teamogy_job_id"] == "J-1"


@pytest.mark.django_db
def test_patch_with_blank_job_id_clears_it(client, workspace, project, create_user, tasks):
    issue = _create_issue(workspace, project, create_user, teamogy_job_id="J-1")

    response = client.patch(f"{_issues_url(workspace, project)}{issue.id}/", {"teamogy_job_id": "   "}, format="json")

    assert response.status_code == status.HTTP_204_NO_CONTENT, response.data
    issue.refresh_from_db()
    assert issue.teamogy_job_id is None


@pytest.mark.django_db
def test_detail_and_list_return_job_id(client, workspace, project, create_user, tasks):
    issue = _create_issue(workspace, project, create_user, teamogy_job_id="J-77")

    detail = client.get(f"{_issues_url(workspace, project)}{issue.id}/")
    assert detail.status_code == status.HTTP_200_OK, detail.data
    assert detail.data["teamogy_job_id"] == "J-77"

    listing = client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/list/?issues={issue.id}")
    assert listing.status_code == status.HTTP_200_OK, listing.data
    assert [row["teamogy_job_id"] for row in listing.data] == ["J-77"]
