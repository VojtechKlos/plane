# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for PQL / JSON filters on the external work item API, in
the request shapes plane-sdk and plane-mcp-server send."""

import json
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status

from plane.db.models import (
    APIToken,
    Cycle,
    CycleIssue,
    Issue,
    IssueLabel,
    Label,
    Module,
    ModuleIssue,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="PQL API", identifier="PQLA", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def other_project(db, workspace, create_user):
    """A second project the token holder is a member of."""
    project = Project.objects.create(name="PQL Other", identifier="PQLO", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def foreign_project(db, workspace):
    """A project the token holder is not a member of."""
    return Project.objects.create(name="Not Mine", identifier="NOPE", workspace=workspace)


def make_state(project, group="backlog"):
    return State.objects.create(name=f"{group}-{uuid4().hex[:4]}", project=project, group=group)


def make_issue(project, name, state=None, created_by=None, **fields):
    issue = Issue(name=name, project=project, state=state or make_state(project), **fields)
    issue.save(created_by_id=created_by.id if created_by else None)
    return issue


@pytest.fixture
def issues(project, other_project, foreign_project):
    backlog = make_state(project, "backlog")
    done = make_state(project, "completed")
    return {
        "urgent": make_issue(project, "urgent todo", backlog, priority="urgent"),
        "high": make_issue(project, "high todo", backlog, priority="high"),
        "done": make_issue(project, "urgent done", done, priority="urgent"),
        "other": make_issue(other_project, "other urgent", priority="urgent"),
        "foreign": make_issue(foreign_project, "foreign urgent", priority="urgent"),
    }


def names(response):
    return {row["name"] for row in response.data["results"]}


def project_url(workspace, project, suffix="work-items"):
    return f"/api/v1/workspaces/{workspace.slug}/projects/{project.id}/{suffix}/"


@pytest.mark.contract
@pytest.mark.django_db
class TestProjectWorkItemListPQL:
    def test_pql_filters_results_and_total(self, api_key_client, workspace, project, issues):
        response = api_key_client.get(
            project_url(workspace, project), {"pql": 'priority = "urgent" AND stateGroup IN openStates()'}
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"urgent todo"}
        assert response.data["total_count"] == 1

    def test_deprecated_issues_route_also_filters(self, api_key_client, workspace, project, issues):
        response = api_key_client.get(project_url(workspace, project, "issues"), {"pql": 'priority = "high"'})
        assert response.status_code == status.HTTP_200_OK
        assert names(response) == {"high todo"}

    def test_invalid_pql_is_a_400_under_the_pql_key(self, api_key_client, workspace, project, issues):
        response = api_key_client.get(project_url(workspace, project), {"pql": "priority = urgent"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Quote string values" in response.data["pql"]

    def test_json_filters(self, api_key_client, workspace, project, issues):
        filters = {"and": [{"priority": "urgent"}, {"state_group__in": "backlog,started"}]}
        response = api_key_client.get(project_url(workspace, project), {"filters": json.dumps(filters)})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"urgent todo"}
        assert response.data["total_count"] == 1

    def test_invalid_json_filters(self, api_key_client, workspace, project, issues):
        response = api_key_client.get(project_url(workspace, project), {"filters": '{"colour": "red"}'})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "colour" in response.data["filters"]

    def test_json_filter_on_labels_does_not_duplicate(self, api_key_client, workspace, project, issues):
        issue = issues["urgent"]
        for name in ("a", "b"):
            label = Label.objects.create(name=name, project=project)
            IssueLabel.objects.create(issue=issue, label=label, project=project)
        label_ids = ",".join(str(i) for i in IssueLabel.objects.filter(issue=issue).values_list("label_id", flat=True))
        response = api_key_client.get(
            project_url(workspace, project), {"filters": json.dumps({"label_id__in": label_ids})}
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert [row["name"] for row in response.data["results"]] == ["urgent todo"]
        assert response.data["total_count"] == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceWorkItemList:
    def url(self, workspace):
        return f"/api/v1/workspaces/{workspace.slug}/work-items/"

    def test_lists_member_projects_only(self, api_key_client, workspace, issues):
        response = api_key_client.get(self.url(workspace))
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"urgent todo", "high todo", "urgent done", "other urgent"}
        assert response.data["total_count"] == 4

    def test_pql_across_projects(self, api_key_client, workspace, issues):
        response = api_key_client.get(
            self.url(workspace), {"pql": 'priority = "urgent" AND stateGroup IN openStates()'}
        )
        assert response.status_code == status.HTTP_200_OK
        assert names(response) == {"urgent todo", "other urgent"}

    def test_pql_project_reference_cannot_reach_a_foreign_project(
        self, api_key_client, workspace, foreign_project, issues
    ):
        response = api_key_client.get(self.url(workspace), {"pql": f'project = "{foreign_project.id}"'})
        assert response.status_code == status.HTTP_200_OK
        assert names(response) == set()

    def test_requires_workspace_membership(self, api_client, workspace, issues):
        outsider = User.objects.create(email="outsider@plane.so", username="outsider")
        token = APIToken.objects.create(user=outsider, label="outsider")
        api_client.credentials(HTTP_X_API_KEY=token.token)
        response = api_client.get(self.url(workspace))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestWorkspaceWorkItemCount:
    def url(self, workspace):
        return f"/api/v1/workspaces/{workspace.slug}/work-items/count/"

    def test_total_without_grouping(self, api_key_client, workspace, issues):
        response = api_key_client.get(self.url(workspace), {"pql": 'priority = "urgent"'})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data == {
            "grouped_by": None,
            "sub_grouped_by": None,
            "total_count": 3,
            "grouped_counts": {},
        }

    def test_group_by_priority(self, api_key_client, workspace, issues):
        response = api_key_client.get(self.url(workspace), {"group_by": "priority"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["total_count"] == 4
        assert response.data["grouped_counts"] == {"urgent": {"count": 3}, "high": {"count": 1}}

    def test_sub_group_by(self, api_key_client, workspace, project, other_project, issues):
        response = api_key_client.get(
            self.url(workspace), {"group_by": "priority", "sub_group_by": "project_id", "pql": 'priority = "urgent"'}
        )
        assert response.status_code == status.HTTP_200_OK
        urgent = response.data["grouped_counts"]["urgent"]
        assert urgent["count"] == 3
        assert urgent["sub_grouped_counts"] == {str(project.id): {"count": 2}, str(other_project.id): {"count": 1}}

    def test_group_by_labels_counts_unlabelled_as_none(self, api_key_client, workspace, project, issues):
        label = Label.objects.create(name="bug", project=project)
        IssueLabel.objects.create(issue=issues["urgent"], label=label, project=project)
        response = api_key_client.get(self.url(workspace), {"group_by": "labels__id"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["grouped_counts"] == {str(label.id): {"count": 1}, "None": {"count": 3}}

    @pytest.mark.parametrize(
        "params, key, message",
        [
            ({"group_by": "colour"}, "group_by", "Invalid value"),
            ({"group_by": "milestone_id"}, "group_by", "not available in Plane Community Edition"),
            ({"sub_group_by": "priority"}, "sub_group_by", "together with group_by"),
            ({"pql": "isOverdue("}, "pql", "Expected"),
        ],
    )
    def test_invalid_requests(self, api_key_client, workspace, issues, params, key, message):
        response = api_key_client.get(self.url(workspace), params)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert message in response.data[key]


@pytest.mark.contract
@pytest.mark.django_db
class TestArchivedCycleModuleLists:
    def test_archived_work_items(self, api_key_client, workspace, project, issues):
        done = make_state(project, "completed")
        make_issue(project, "archived urgent", done, priority="urgent", archived_at=timezone.now().date())
        make_issue(project, "archived low", done, priority="low", archived_at=timezone.now().date())

        url = project_url(workspace, project, "archived-work-items")
        response = api_key_client.get(url)
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"archived urgent", "archived low"}

        response = api_key_client.get(url, {"pql": 'priority = "urgent"'})
        assert names(response) == {"archived urgent"}
        assert response.data["total_count"] == 1

    def test_cycle_work_items(self, api_key_client, workspace, project, create_user, issues):
        cycle = Cycle.objects.create(name="Sprint", project=project, owned_by=create_user)
        for key in ("urgent", "high"):
            CycleIssue.objects.create(cycle=cycle, issue=issues[key], project=project)
        url = project_url(workspace, project, f"cycles/{cycle.id}/cycle-issues")
        response = api_key_client.get(url, {"pql": 'priority = "high"'})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"high todo"}

        assert api_key_client.get(url, {"pql": "priority ="}).status_code == status.HTTP_400_BAD_REQUEST

    def test_module_work_items(self, api_key_client, workspace, project, issues):
        module = Module.objects.create(name="Mod", project=project)
        for key in ("urgent", "high"):
            ModuleIssue.objects.create(module=module, issue=issues[key], project=project)
        url = project_url(workspace, project, f"modules/{module.id}/module-issues")
        response = api_key_client.get(url, {"pql": 'priority = "urgent"'})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"urgent todo"}


@pytest.mark.contract
@pytest.mark.django_db
class TestRestrictedGuest:
    @pytest.fixture
    def guest_client(self, api_client, workspace, project):
        guest = User.objects.create(email="pql-guest@plane.so", username="pql_guest")
        WorkspaceMember.objects.create(workspace=workspace, member=guest, role=5)
        ProjectMember.objects.create(project=project, member=guest, workspace=workspace, role=5)
        make_issue(project, "guest urgent", priority="urgent", created_by=guest)
        token = APIToken.objects.create(user=guest, label="guest")
        api_client.credentials(HTTP_X_API_KEY=token.token)
        return api_client

    def test_guest_only_sees_own_items(self, guest_client, workspace, project, issues):
        response = guest_client.get(project_url(workspace, project), {"pql": 'priority = "urgent"'})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert names(response) == {"guest urgent"}
        assert response.data["total_count"] == 1

        response = guest_client.get(f"/api/v1/workspaces/{workspace.slug}/work-items/", {"pql": 'priority = "urgent"'})
        assert names(response) == {"guest urgent"}

        response = guest_client.get(f"/api/v1/workspaces/{workspace.slug}/work-items/count/")
        assert response.data["total_count"] == 1
