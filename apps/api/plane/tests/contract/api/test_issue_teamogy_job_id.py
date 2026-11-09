# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""The external API reads and writes the optional Teamogy job ID of a work item."""

from unittest import mock

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import APIToken, Issue, Project, ProjectMember

pytestmark = pytest.mark.contract


@pytest.fixture(autouse=True)
def _no_activity():
    with (
        mock.patch("plane.api.views.issue.issue_activity"),
        mock.patch("plane.api.views.issue.model_activity"),
    ):
        yield


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Teamogy", identifier="TMG", workspace=workspace, created_by=create_user)
    ProjectMember.objects.create(workspace=workspace, project=project, member=create_user, role=20)
    return project


@pytest.fixture
def client(create_user):
    token = APIToken.objects.create(user=create_user, label="Teamogy", token="teamogy-job-id-token")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=token.token)
    return client


def _work_items_url(workspace, project):
    return f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/work-items/"


@pytest.mark.django_db
def test_create_and_read_job_id(client, workspace, project):
    response = client.post(
        _work_items_url(workspace, project), {"name": "With job", "teamogy_job_id": " J-5 "}, format="json"
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data["teamogy_job_id"] == "J-5"

    detail = client.get(f"{_work_items_url(workspace, project)}{response.data['id']}/")
    assert detail.status_code == status.HTTP_200_OK, detail.data
    assert detail.data["teamogy_job_id"] == "J-5"


@pytest.mark.django_db
def test_patch_with_empty_job_id_clears_it(client, workspace, project, create_user):
    issue = Issue(name="Work item", workspace=workspace, project=project, teamogy_job_id="J-5")
    issue.save(created_by_id=create_user.id)

    response = client.patch(f"{_work_items_url(workspace, project)}{issue.id}/", {"teamogy_job_id": ""}, format="json")

    assert response.status_code == status.HTTP_200_OK, response.data
    issue.refresh_from_db()
    assert issue.teamogy_job_id is None
