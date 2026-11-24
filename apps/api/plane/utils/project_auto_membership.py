# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Add every workspace member to every project of the workspace.

On unless ``PROJECT_AUTO_MEMBERSHIP`` is turned off. Members get the project role
``PROJECT_AUTO_MEMBERSHIP_ROLE`` (capped at their workspace role), by default
Member, so work items can be assigned to them. With the Guest role the
project's ``guest_view_all_features`` is turned on, so they can read and
comment on every work item without being able to edit.

Secret (private) projects are skipped: they stay invite-only.

Only missing memberships are created: a membership an admin deactivated, or
one the member left, is never reactivated.
"""

# Django imports
from django.conf import settings
from django.db import transaction
from django.utils import timezone

# Module imports
from plane.db.models import Project, ProjectMember, ProjectUserProperty, WorkspaceMember
from plane.db.models.project import ROLE
from plane.utils.exception_logger import log_exception


SECRET = 0  # Project.network


def eligible_member_roles(workspace_id, *, user_ids=None):
    """{member_id: workspace role} of the active, non-bot workspace members above GUEST."""
    workspace_members = WorkspaceMember.objects.filter(
        workspace_id=workspace_id,
        is_active=True,
        role__gt=ROLE.GUEST.value,
        member__is_bot=False,
        member__is_active=True,
    )
    if user_ids is not None:
        workspace_members = workspace_members.filter(member_id__in=user_ids)
    return dict(workspace_members.values_list("member_id", "role"))


def missing_project_memberships(workspace_id, *, project_ids=None, user_ids=None):
    """(project_id, member_id, role) for every eligible member of the workspace
    who has no membership row in an eligible project.

    Eligible members are active, non-bot workspace members above GUEST;
    eligible projects are the workspace's non-archived, non-secret projects.
    """
    member_roles = eligible_member_roles(workspace_id, user_ids=user_ids)

    projects = Project.objects.filter(workspace_id=workspace_id, archived_at__isnull=True).exclude(network=SECRET)
    if project_ids is not None:
        projects = projects.filter(id__in=project_ids)
    eligible_project_ids = list(projects.values_list("id", flat=True))

    if not member_roles or not eligible_project_ids:
        return []

    # Inactive rows count too, so removals and leaves stick
    existing = set(
        ProjectMember.objects.filter(project_id__in=eligible_project_ids, member_id__in=list(member_roles)).values_list(
            "project_id", "member_id"
        )
    )

    role = settings.PROJECT_AUTO_MEMBERSHIP_ROLE
    return [
        (project_id, member_id, min(role, workspace_role))
        for project_id in eligible_project_ids
        for member_id, workspace_role in member_roles.items()
        if (project_id, member_id) not in existing
    ]


def ensure_project_memberships(workspace_id, *, project_ids=None, user_ids=None):
    """Create the missing memberships and return how many were created."""
    missing = missing_project_memberships(workspace_id, project_ids=project_ids, user_ids=user_ids)
    if not missing:
        return 0

    with transaction.atomic():
        ProjectMember.objects.bulk_create(
            [
                ProjectMember(project_id=project_id, member_id=member_id, workspace_id=workspace_id, role=role)
                for project_id, member_id, role in missing
            ],
            batch_size=500,
            ignore_conflicts=True,
        )
        # bulk_create skips ProjectMember.save(), which creates these
        ProjectUserProperty.objects.bulk_create(
            [
                ProjectUserProperty(project_id=project_id, user_id=member_id, workspace_id=workspace_id)
                for project_id, member_id, _ in missing
            ],
            batch_size=500,
            ignore_conflicts=True,
        )
        guest_project_ids = {project_id for project_id, _, role in missing if role == ROLE.GUEST.value}
        if guest_project_ids:
            Project.objects.filter(id__in=guest_project_ids, guest_view_all_features=False).update(
                guest_view_all_features=True
            )

    return len(missing)


def _project_guests(workspace_id, member_ids):
    """Active project Guest rows of the given workspace members."""
    return ProjectMember.objects.filter(
        workspace_id=workspace_id, is_active=True, role=ROLE.GUEST.value, member_id__in=member_ids
    )


def upgrade_guests(workspace_id, *, dry_run=False):
    """One-off: raise workspace members who are a Guest of a non-secret project
    to ``PROJECT_AUTO_MEMBERSHIP_ROLE`` (capped at their workspace role).

    For memberships created while the role was Guest. A Guest someone set by
    hand cannot be told apart, so it is raised too. Returns the number of rows.
    """
    member_roles = eligible_member_roles(workspace_id)
    count = 0
    for workspace_role in set(member_roles.values()):
        role = min(settings.PROJECT_AUTO_MEMBERSHIP_ROLE, workspace_role)
        if role <= ROLE.GUEST.value:
            continue
        member_ids = [member_id for member_id, value in member_roles.items() if value == workspace_role]
        guests = _project_guests(workspace_id, member_ids).exclude(project__network=SECRET)
        count += guests.count() if dry_run else guests.update(role=role, updated_at=timezone.now())
    return count


def remove_secret_project_guests(workspace_id, *, dry_run=False):
    """One-off: remove workspace members who are a Guest of a secret project,
    where earlier versions added them. Returns the number of rows.

    The rows are soft-deleted, not deactivated, so the member is added again
    if the project is later made public.
    """
    guests = _project_guests(workspace_id, list(eligible_member_roles(workspace_id))).filter(project__network=SECRET)
    if dry_run:
        return guests.count()
    now = timezone.now()
    return guests.update(deleted_at=now, updated_at=now)


def auto_join_projects(workspace_id, *, project_ids=None, user_ids=None):
    """Hook for project creation and workspace joins: a no-op unless
    ``PROJECT_AUTO_MEMBERSHIP`` is on, and never fails the calling request.
    The periodic sync task retries anything missed here."""
    if not settings.PROJECT_AUTO_MEMBERSHIP:
        return
    try:
        # A savepoint, so a failure here leaves the caller's transaction usable
        with transaction.atomic():
            ensure_project_memberships(workspace_id, project_ids=project_ids, user_ids=user_ids)
    except Exception as e:
        log_exception(e)
