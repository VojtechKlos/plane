# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL compiled against the database: each field, function and flag."""

import datetime
import zoneinfo
from uuid import uuid4

import pytest
from django.utils import timezone

from plane.db.models import (
    Cycle,
    CycleIssue,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueRelation,
    Label,
    Project,
    ProjectMember,
    State,
    User,
    WorkspaceMember,
)
from plane.utils.pql import PQLError, compile_pql

TODAY = datetime.date(2026, 7, 15)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    """Pin "today" so date functions are deterministic."""
    monkeypatch.setattr(timezone, "localdate", lambda *args, **kwargs: TODAY)


@pytest.fixture
def project(db, workspace, create_user):
    project = Project.objects.create(name="PQL Project", identifier="PQL", workspace=workspace)
    ProjectMember.objects.create(project=project, member=create_user, workspace=workspace, role=20)
    return project


@pytest.fixture
def states(project):
    return {
        group: State.objects.create(name=group.title(), project=project, group=group, default=group == "backlog")
        for group in ("backlog", "started", "completed")
    }


@pytest.fixture
def other_user(db, workspace):
    user = User.objects.create(email=f"other-{uuid4().hex[:8]}@plane.so", username=f"other_{uuid4().hex[:8]}")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    return user


def make_issue(project, name, state, created_by=None, **fields):
    issue = Issue(name=name, project=project, state=state, **fields)
    # BaseModel.save() takes the author from the request, unless given
    issue.save(created_by_id=created_by.id if created_by else None)
    return issue


def names(query, user, project):
    compiled = compile_pql(query, user_id=user.id, workspace_slug=project.workspace.slug)
    queryset = compiled.base_queryset().filter(project=project).filter(compiled.q)
    return set(queryset.values_list("name", flat=True))


@pytest.mark.unit
@pytest.mark.django_db
class TestScalarFields:
    def test_priority(self, project, states, create_user):
        make_issue(project, "urgent", states["backlog"], priority="urgent")
        make_issue(project, "low", states["backlog"], priority="low")
        assert names('priority = "urgent"', create_user, project) == {"urgent"}
        assert names('priority IN ("urgent", "low")', create_user, project) == {"urgent", "low"}
        assert names('priority NOT IN ("urgent")', create_user, project) == {"low"}
        assert names('priority != "LOW"', create_user, project) == {"urgent"}

    def test_invalid_enum_value(self, project, create_user):
        with pytest.raises(PQLError, match="Valid values"):
            names('priority = "critical"', create_user, project)

    def test_state_group_and_functions(self, project, states, create_user):
        make_issue(project, "todo", states["backlog"])
        make_issue(project, "doing", states["started"])
        make_issue(project, "done", states["completed"])
        assert names("stateGroup IN openStates()", create_user, project) == {"todo", "doing"}
        assert names("stateGroup IN closedStates()", create_user, project) == {"done"}
        assert names("stateGroup = activeStates()", create_user, project) == {"doing"}
        assert names('stateGroup NOT IN ("backlog")', create_user, project) == {"doing", "done"}

    def test_state_uuid_and_validation(self, project, states, create_user):
        make_issue(project, "doing", states["started"])
        make_issue(project, "todo", states["backlog"])
        assert names(f'state = "{states["started"].id}"', create_user, project) == {"doing"}
        with pytest.raises(PQLError, match="expects a UUID"):
            names('state = "Started"', create_user, project)

    def test_title_and_text(self, project, states, create_user):
        make_issue(project, "Capex review", states["backlog"])
        make_issue(project, "Other", states["backlog"], description_html="<p>mentions capex in the body</p>")
        assert names('title ~ "capex"', create_user, project) == {"Capex review"}
        assert names('text ~ "capex"', create_user, project) == {"Capex review", "Other"}
        assert names('title = "capex REVIEW"', create_user, project) == {"Capex review"}
        with pytest.raises(PQLError, match="not supported for 'text'"):
            names('text = "capex"', create_user, project)

    def test_id(self, project, states, create_user):
        first = make_issue(project, "first", states["backlog"])
        make_issue(project, "second", states["backlog"])
        assert names(f'id = "PQL-{first.sequence_id}"', create_user, project) == {"first"}
        assert names(f'id = "pql-{first.sequence_id}"', create_user, project) == {"first"}
        assert names(f'id != "PQL-{first.sequence_id}"', create_user, project) == {"second"}
        assert names('id ~ "PQL"', create_user, project) == {"first", "second"}
        assert names(f'id = "{first.id}"', create_user, project) == {"first"}
        with pytest.raises(PQLError, match="Invalid work item id"):
            names('id = "first"', create_user, project)

    def test_created_by_current_user(self, project, states, create_user, other_user):
        make_issue(project, "mine", states["backlog"], created_by=create_user)
        make_issue(project, "theirs", states["backlog"], created_by=other_user)
        assert names("createdBy = currentUser()", create_user, project) == {"mine"}
        assert names("createdBy != currentUser()", create_user, project) == {"theirs"}

    def test_case_insensitive_field_and_function_names(self, project, states, create_user):
        make_issue(project, "urgent", states["backlog"], priority="urgent")
        assert names('PRIORITY = "urgent" AND stategroup in OPENSTATES()', create_user, project) == {"urgent"}


@pytest.mark.unit
@pytest.mark.django_db
class TestRelations:
    def test_two_assignees_must_both_match(self, project, states, create_user, other_user):
        both = make_issue(project, "both", states["backlog"])
        only_me = make_issue(project, "only me", states["backlog"])
        make_issue(project, "nobody", states["backlog"])
        for user in (create_user, other_user):
            IssueAssignee.objects.create(issue=both, assignee=user, project=project)
        IssueAssignee.objects.create(issue=only_me, assignee=create_user, project=project)

        query = f'assignee = currentUser() AND assignee = "{other_user.id}"'
        assert names(query, create_user, project) == {"both"}
        assert names("assignee = currentUser()", create_user, project) == {"both", "only me"}
        assert names(f'NOT assignee = "{other_user.id}"', create_user, project) == {"only me", "nobody"}
        assert names(f'assignee != "{other_user.id}"', create_user, project) == {"only me", "nobody"}
        assert names("hasNoAssignee()", create_user, project) == {"nobody"}
        assert names("assignee IS NOT EMPTY", create_user, project) == {"both", "only me"}

    def test_soft_deleted_assignee_is_ignored(self, project, states, create_user):
        issue = make_issue(project, "unassigned again", states["backlog"])
        assignee = IssueAssignee.objects.create(issue=issue, assignee=create_user, project=project)
        assignee.deleted_at = timezone.now()
        assignee.save()
        assert names("assignee = currentUser()", create_user, project) == set()
        assert names("hasNoAssignee()", create_user, project) == {"unassigned again"}

    def test_workspace_members_function(self, project, states, create_user, other_user):
        issue = make_issue(project, "assigned", states["backlog"])
        make_issue(project, "free", states["backlog"])
        IssueAssignee.objects.create(issue=issue, assignee=other_user, project=project)
        assert names("assignee IN workspaceMembers()", create_user, project) == {"assigned"}
        assert names(f'assignee IN membersOf("project:{project.id}")', create_user, project) == set()

    def test_labels(self, project, states, create_user):
        label = Label.objects.create(name="bug", project=project)
        labelled = make_issue(project, "labelled", states["backlog"])
        make_issue(project, "plain", states["backlog"])
        IssueLabel.objects.create(issue=labelled, label=label, project=project)
        assert names(f'label = "{label.id}"', create_user, project) == {"labelled"}
        assert names("label IS EMPTY", create_user, project) == {"plain"}
        assert names("hasNoLabel()", create_user, project) == {"plain"}

    def test_active_cycle(self, project, states, create_user):
        now = timezone.now()
        active = Cycle.objects.create(
            name="Now",
            project=project,
            owned_by=create_user,
            start_date=now - datetime.timedelta(days=1),
            end_date=now + datetime.timedelta(days=1),
        )
        past = Cycle.objects.create(
            name="Past",
            project=project,
            owned_by=create_user,
            start_date=now - datetime.timedelta(days=30),
            end_date=now - datetime.timedelta(days=20),
        )
        in_active = make_issue(project, "in active", states["backlog"])
        in_past = make_issue(project, "in past", states["backlog"])
        make_issue(project, "no cycle", states["backlog"])
        CycleIssue.objects.create(cycle=active, issue=in_active, project=project)
        CycleIssue.objects.create(cycle=past, issue=in_past, project=project)
        assert names("cycle IN activeCycle()", create_user, project) == {"in active"}
        assert names("cycle IN completedCycles()", create_user, project) == {"in past"}
        assert names("cycle IS EMPTY", create_user, project) == {"no cycle"}

    def test_parent_child(self, project, states, create_user):
        parent = make_issue(project, "parent", states["backlog"])
        make_issue(project, "child", states["backlog"], parent=parent)
        make_issue(project, "loner", states["backlog"])
        child_id = Issue.objects.get(name="child").sequence_id
        assert names(f'childOf("PQL-{parent.sequence_id}")', create_user, project) == {"child"}
        assert names(f'parentOf("PQL-{child_id}")', create_user, project) == {"parent"}
        assert names("isTopLevel()", create_user, project) == {"parent", "loner"}
        assert names("isSubWorkItem()", create_user, project) == {"child"}
        assert names("hasChildren()", create_user, project) == {"parent"}

    def test_blocking_relations(self, project, states, create_user):
        blocked = make_issue(project, "blocked", states["backlog"])
        blocker = make_issue(project, "blocker", states["backlog"])
        # "blocked is blocked by blocker"
        IssueRelation.objects.create(issue=blocked, related_issue=blocker, relation_type="blocked_by", project=project)
        assert names(f'blockedBy("PQL-{blocker.sequence_id}")', create_user, project) == {"blocked"}
        assert names(f'blocks("PQL-{blocked.sequence_id}")', create_user, project) == {"blocker"}

    def test_linked_to_is_symmetric(self, project, states, create_user):
        first = make_issue(project, "first", states["backlog"])
        second = make_issue(project, "second", states["backlog"])
        IssueRelation.objects.create(issue=first, related_issue=second, relation_type="relates_to", project=project)
        assert names(f'linkedTo("PQL-{first.sequence_id}")', create_user, project) == {"second"}
        assert names(f'linkedTo("PQL-{second.sequence_id}")', create_user, project) == {"first"}


@pytest.mark.unit
@pytest.mark.django_db
class TestDates:
    def test_due_date_comparisons(self, project, states, create_user):
        make_issue(project, "overdue", states["backlog"], target_date=TODAY - datetime.timedelta(days=3))
        make_issue(project, "done late", states["completed"], target_date=TODAY - datetime.timedelta(days=3))
        make_issue(project, "upcoming", states["backlog"], target_date=TODAY + datetime.timedelta(days=3))
        make_issue(project, "no date", states["backlog"])
        assert names("dueDate < today()", create_user, project) == {"overdue", "done late"}
        assert names("isOverdue()", create_user, project) == {"overdue"}
        assert names("dueDate BETWEEN (daysAgo(7), today())", create_user, project) == {"overdue", "done late"}
        assert names('dueDate >= "2026-07-16"', create_user, project) == {"upcoming"}
        assert names("dueDate IS NULL", create_user, project) == {"no date"}
        assert names("dueDate <= daysFromNow(3) AND dueDate > today()", create_user, project) == {"upcoming"}

    def test_calendar_functions(self, project, states, create_user):
        # TODAY is Wednesday 2026-07-15
        make_issue(project, "monday", states["backlog"], start_date=datetime.date(2026, 7, 13))
        make_issue(project, "sunday", states["backlog"], start_date=datetime.date(2026, 7, 19))
        make_issue(project, "june", states["backlog"], start_date=datetime.date(2026, 6, 30))
        assert names("startDate = startOfWeek()", create_user, project) == {"monday"}
        assert names("startDate = endOfWeek()", create_user, project) == {"sunday"}
        assert names("startDate < startOfMonth()", create_user, project) == {"june"}
        assert names("startDate >= monthsAgo(1)", create_user, project) == {"monday", "sunday", "june"}

    def test_created_at_uses_the_active_timezone(self, project, states, create_user):
        issue = make_issue(project, "late evening utc", states["backlog"])
        Issue.objects.filter(pk=issue.pk).update(
            created_at=datetime.datetime(2026, 7, 30, 20, 0, tzinfo=datetime.timezone.utc)
        )
        timezone.activate(zoneinfo.ZoneInfo("Asia/Tokyo"))
        try:
            assert names('createdAt = "2026-07-31"', create_user, project) == {"late evening utc"}
        finally:
            timezone.deactivate()
        assert names('createdAt = "2026-07-30"', create_user, project) == {"late evening utc"}

    def test_invalid_date(self, project, create_user):
        with pytest.raises(PQLError, match="Invalid date"):
            names('dueDate < "next week"', create_user, project)
        with pytest.raises(PQLError, match="whole number"):
            names("dueDate < daysAgo(1.5)", create_user, project)
        with pytest.raises(PQLError, match="not a date function"):
            names("dueDate < currentUser()", create_user, project)


@pytest.mark.unit
@pytest.mark.django_db
class TestHiddenWorkItems:
    def test_default_scope_hides_drafts_and_archived(self, project, states, create_user):
        make_issue(project, "normal", states["backlog"])
        make_issue(project, "draft", states["backlog"], is_draft=True)
        make_issue(project, "archived", states["completed"], archived_at=TODAY)
        assert names('priority = "none"', create_user, project) == {"normal"}

    def test_draft_and_archived_flags(self, project, states, create_user):
        make_issue(project, "normal", states["backlog"])
        make_issue(project, "draft", states["backlog"], is_draft=True)
        make_issue(project, "archived", states["completed"], archived_at=TODAY)
        assert names("isDraft()", create_user, project) == {"draft"}
        assert names("isDraft = true", create_user, project) == {"draft"}
        assert names("isArchived()", create_user, project) == {"archived"}
        assert names("NOT isArchived()", create_user, project) == {"normal"}
        assert names("isDraft = false", create_user, project) == {"normal"}


@pytest.mark.unit
@pytest.mark.django_db
class TestUnsupported:
    @pytest.mark.parametrize(
        "query, message",
        [
            ('cf["4c7d"] = "x"', "Custom properties"),
            ('milestone = "4c7d"', "not available in Plane Community Edition"),
            ('assignee IN membersOf("teamspace:abc")', "Teamspaces are not available"),
            ('colour = "red"', "Unknown field 'colour'"),
            ("frobnicate()", "Unknown function frobnicate()"),
            ("today()", "is a value"),
            ('priority ~ "hi"', "not supported for 'priority'"),
        ],
    )
    def test_errors(self, project, create_user, query, message):
        with pytest.raises(PQLError, match=message):
            names(query, create_user, project)
