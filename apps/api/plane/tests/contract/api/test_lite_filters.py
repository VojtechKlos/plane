# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the filters plane-sdk sends to the lite list endpoints:
member filters on members-lite / project-members-lite, and the cycle status
on cycles-lite and the full cycle list."""

import datetime

import pytest
from django.utils import timezone
from rest_framework import status

from plane.db.models import Cycle, Project, ProjectMember, User, WorkspaceMember


def make_member(workspace, email, first_name, role=15, is_active=True, is_bot=False):
    user = User.objects.create(
        email=email, username=email.split("@")[0], first_name=first_name, display_name=first_name, is_bot=is_bot
    )
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role, is_active=is_active)
    return user


@pytest.fixture
def members(db, workspace):
    return {
        "alice": make_member(workspace, "alice@crs.example", "Alice"),
        "bob": make_member(workspace, "bob@other.example", "Bob", role=5),
        "carol": make_member(workspace, "carol@crs.example", "Carol", is_active=False),
        "robot": make_member(workspace, "robot@crs.example", "Robot", is_bot=True),
    }


def emails(response):
    return {row["email"] for row in response.data["results"]}


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceMembersLiteFilters:
    def url(self, workspace):
        return f"/api/v1/workspaces/{workspace.slug}/members-lite/"

    def test_text_filters_ignore_case_and_combine(self, api_key_client, workspace, members):
        response = api_key_client.get(self.url(workspace), {"email": "CRS.EXAMPLE"})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert emails(response) == {"alice@crs.example", "carol@crs.example", "robot@crs.example"}

        response = api_key_client.get(self.url(workspace), {"email": "crs", "first_name": "ali"})
        assert emails(response) == {"alice@crs.example"}

    def test_role_slug(self, api_key_client, workspace, members):
        response = api_key_client.get(self.url(workspace), {"role_slug": "guest"})
        assert emails(response) == {"bob@other.example"}
        assert response.data["results"][0]["role_slug"] == "guest"

    def test_boolean_filters(self, api_key_client, workspace, members):
        response = api_key_client.get(self.url(workspace), {"is_active": "false"})
        assert emails(response) == {"carol@crs.example"}

        response = api_key_client.get(self.url(workspace), {"is_bot": "true"})
        assert emails(response) == {"robot@crs.example"}

    def test_order_by_member_field(self, api_key_client, workspace, members):
        response = api_key_client.get(self.url(workspace), {"order_by": "-first_name", "email": "crs"})
        assert [row["first_name"] for row in response.data["results"]] == ["Robot", "Carol", "Alice"]

    @pytest.mark.parametrize(
        "params, key", [({"is_active": "yes"}, "is_active"), ({"role_slug": "owner"}, "role_slug")]
    )
    def test_invalid_values(self, api_key_client, workspace, members, params, key):
        response = api_key_client.get(self.url(workspace), params)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert key in response.data


@pytest.mark.contract
@pytest.mark.django_db
class TestProjectMembersLiteFilters:
    def test_role_slug_and_text(self, api_key_client, workspace, create_user, members):
        project = Project.objects.create(name="Lite Filters", identifier="LIF", workspace=workspace)
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
        ProjectMember.objects.create(project=project, member=members["alice"], workspace=workspace, role=5)
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/project-members-lite/"

        response = api_key_client.get(url, {"role_slug": "guest"})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert emails(response) == {"alice@crs.example"}

        response = api_key_client.get(url, {"display_name": "ALI"})
        assert emails(response) == {"alice@crs.example"}


@pytest.mark.contract
@pytest.mark.django_db
class TestCycleStatus:
    @pytest.fixture
    def project(self, workspace, create_user):
        project = Project.objects.create(name="Cycle Status", identifier="CST", workspace=workspace)
        ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
        now = timezone.now()
        day = datetime.timedelta(days=1)
        for name, start, end in [
            ("current", now - day, now + day),
            ("upcoming", now + 5 * day, now + 10 * day),
            ("completed", now - 10 * day, now - 5 * day),
            ("draft", None, None),
        ]:
            Cycle.objects.create(name=name, project=project, owned_by=create_user, start_date=start, end_date=end)
        return project

    @pytest.mark.parametrize(
        "cycle_status, expected",
        [
            ("current", {"current"}),
            ("upcoming", {"upcoming"}),
            ("completed", {"completed"}),
            ("draft", {"draft"}),
            ("incomplete", {"current", "upcoming", "draft"}),
        ],
    )
    def test_cycles_lite_status(self, api_key_client, workspace, project, cycle_status, expected):
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/cycles-lite/"
        response = api_key_client.get(url, {"status": cycle_status})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert {row["name"] for row in response.data["results"]} == expected

    def test_cycles_lite_without_status_lists_all(self, api_key_client, workspace, project):
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/cycles-lite/"
        response = api_key_client.get(url)
        assert {row["name"] for row in response.data["results"]} == {"current", "upcoming", "completed", "draft"}

    def test_cycles_lite_invalid_status(self, api_key_client, workspace, project):
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/cycles-lite/"
        response = api_key_client.get(url, {"status": "active"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Valid values" in response.data["status"]

    def test_full_cycle_list_accepts_status(self, api_key_client, workspace, project):
        url = f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/cycles/"
        response = api_key_client.get(url, {"status": "upcoming"})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert {row["name"] for row in response.data["results"]} == {"upcoming"}

        # cycle_view still works, and status wins when both are sent
        response = api_key_client.get(url, {"cycle_view": "draft"})
        assert {row["name"] for row in response.data["results"]} == {"draft"}
        response = api_key_client.get(url, {"status": "completed", "cycle_view": "draft"})
        assert {row["name"] for row in response.data["results"]} == {"completed"}
