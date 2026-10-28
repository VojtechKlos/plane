# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PROJECT_AUTO_MEMBERSHIP adds every workspace member (above Guest) to every
project, by default as a Guest who sees all work items and can comment."""

from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from plane.bgtasks.project_auto_membership_task import sync_project_memberships
from plane.db.models import Issue, Project, ProjectMember, ProjectUserProperty, State, User, WorkspaceMember
from plane.utils.project_auto_membership import ensure_project_memberships

ADMIN = 20
MEMBER = 15
GUEST = 5


@pytest.fixture
def auto_membership(settings):
    settings.PROJECT_AUTO_MEMBERSHIP = True
    settings.PROJECT_AUTO_MEMBERSHIP_ROLE = GUEST
    return settings


def make_user(workspace, role, **fields):
    suffix = uuid4().hex[:8]
    user = User.objects.create(email=f"user-{suffix}@plane.so", username=f"user_{suffix}", **fields)
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=role)
    return user


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="Existing", identifier="EXI", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=ADMIN)
    return project


@pytest.fixture
def member(db, workspace):
    return make_user(workspace, MEMBER)


def membership(project, user):
    return ProjectMember.objects.filter(project=project, member=user).first()


@pytest.mark.contract
@pytest.mark.django_db
class TestEnsureProjectMemberships:
    def test_adds_members_as_guests_who_see_everything(self, auto_membership, workspace, project, member):
        assert ensure_project_memberships(workspace.id) == 1

        row = membership(project, member)
        assert row.role == GUEST
        assert row.is_active is True
        assert ProjectUserProperty.objects.filter(project=project, user=member).exists()
        project.refresh_from_db()
        assert project.guest_view_all_features is True

    def test_skips_workspace_guests_bots_and_inactive(self, auto_membership, workspace, project):
        make_user(workspace, GUEST)
        make_user(workspace, MEMBER, is_bot=True)
        inactive = make_user(workspace, MEMBER)
        WorkspaceMember.objects.filter(member=inactive).update(is_active=False)

        assert ensure_project_memberships(workspace.id) == 0

    def test_role_is_capped_at_the_workspace_role(self, auto_membership, workspace, project, member):
        auto_membership.PROJECT_AUTO_MEMBERSHIP_ROLE = ADMIN
        ensure_project_memberships(workspace.id)
        assert membership(project, member).role == MEMBER

    def test_member_role_leaves_guest_setting_alone(self, auto_membership, workspace, project, member):
        auto_membership.PROJECT_AUTO_MEMBERSHIP_ROLE = MEMBER
        ensure_project_memberships(workspace.id)
        assert membership(project, member).role == MEMBER
        project.refresh_from_db()
        assert project.guest_view_all_features is False

    def test_removed_member_is_not_re_added(self, auto_membership, workspace, project, member):
        ProjectMember.objects.create(project=project, member=member, workspace=workspace, role=MEMBER, is_active=False)

        assert ensure_project_memberships(workspace.id) == 0
        assert membership(project, member).is_active is False

    def test_existing_role_is_kept_and_rerun_is_a_no_op(self, auto_membership, workspace, project, member):
        ProjectMember.objects.create(project=project, member=member, workspace=workspace, role=MEMBER)
        second = Project.objects.create(name="Second", identifier="SEC", workspace=workspace)

        assert ensure_project_memberships(workspace.id) == 2  # member and the admin in "Second"
        assert ensure_project_memberships(workspace.id) == 0
        assert membership(project, member).role == MEMBER
        assert membership(second, member).role == GUEST

    def test_archived_projects_are_skipped(self, auto_membership, workspace, project, member):
        Project.objects.filter(pk=project.pk).update(archived_at="2026-01-01T00:00:00Z")
        assert ensure_project_memberships(workspace.id) == 0


@pytest.mark.contract
@pytest.mark.django_db
class TestHooks:
    def test_new_project_gets_every_member(self, auto_membership, session_client, workspace, member):
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/",
            {"name": "Brand New", "identifier": "NEW"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert membership(Project.objects.get(identifier="NEW"), member).role == GUEST

    def test_disabled_when_off(self, settings, session_client, workspace, member):
        settings.PROJECT_AUTO_MEMBERSHIP = False
        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/",
            {"name": "Brand New", "identifier": "NEW"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert membership(Project.objects.get(identifier="NEW"), member) is None

    def test_guest_promoted_to_member_joins_projects(self, auto_membership, session_client, workspace, project):
        guest = make_user(workspace, GUEST)
        row = WorkspaceMember.objects.get(workspace=workspace, member=guest)

        response = session_client.patch(
            f"/api/workspaces/{workspace.slug}/members/{row.id}/", {"role": MEMBER}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert membership(project, guest).role == GUEST

    def test_periodic_task(self, auto_membership, workspace, project, member):
        sync_project_memberships()
        assert membership(project, member) is not None

    def test_periodic_task_is_a_no_op_when_disabled(self, settings, workspace, project, member):
        settings.PROJECT_AUTO_MEMBERSHIP = False
        sync_project_memberships()
        assert membership(project, member) is None

    def test_management_command(self, workspace, project, member):
        out = StringIO()
        call_command("sync_project_memberships", "--dry-run", stdout=out)
        assert "Would add 1 membership(s)" in out.getvalue()
        assert membership(project, member) is None

        call_command("sync_project_memberships", "--workspace", workspace.slug, stdout=StringIO())
        assert membership(project, member).role == GUEST


@pytest.mark.contract
@pytest.mark.django_db
class TestAutoAddedGuestAccess:
    @pytest.fixture
    def issue(self, project, create_user):
        state = State.objects.create(name="Todo", project=project, group="backlog", default=True)
        issue = Issue(name="Someone else's work", project=project, state=state)
        issue.save(created_by_id=create_user.id)
        return issue

    @pytest.fixture
    def guest_client(self, auto_membership, workspace, project, member, issue):
        ensure_project_memberships(workspace.id)
        return client_for(member)

    def test_can_list_and_read_all_work_items(self, guest_client, workspace, project, issue):
        response = guest_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/")
        assert response.status_code == status.HTTP_200_OK, response.data
        assert str(issue.id) in str(response.data)

        response = guest_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/")
        assert response.status_code == status.HTTP_200_OK, response.data

    def test_can_comment(self, guest_client, workspace, project, issue):
        response = guest_client.post(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/comments/",
            {"comment_html": "<p>Looks good</p>"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_cannot_create_or_edit_work_items(self, guest_client, workspace, project, issue):
        base = f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/"
        assert guest_client.post(base, {"name": "Nope"}, format="json").status_code == status.HTTP_403_FORBIDDEN
        response = guest_client.patch(f"{base}{issue.id}/", {"name": "Renamed"}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_join_upgrades_to_the_workspace_role(self, guest_client, workspace, project, member):
        response = guest_client.post(
            f"/api/users/me/workspaces/{workspace.slug}/projects/invitations/",
            {"project_ids": [str(project.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert membership(project, member).role == MEMBER

    def test_join_cannot_upgrade_in_a_secret_project(self, guest_client, workspace, project, member):
        Project.objects.filter(pk=project.pk).update(network=0)
        response = guest_client.post(
            f"/api/users/me/workspaces/{workspace.slug}/projects/invitations/",
            {"project_ids": [str(project.id)]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert membership(project, member).role == GUEST
