# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings

# Third party imports
from celery import shared_task

# Module imports
from plane.db.models import Workspace
from plane.utils.exception_logger import log_exception
from plane.utils.project_auto_membership import ensure_project_memberships


@shared_task
def sync_project_memberships():
    """Add every workspace member to every project they are missing from.

    Catches memberships the request-time hooks missed, e.g. members added
    through a path without a hook.
    """
    if not settings.PROJECT_AUTO_MEMBERSHIP:
        return

    for workspace_id in Workspace.objects.values_list("id", flat=True):
        try:
            ensure_project_memberships(workspace_id)
        except Exception as e:
            log_exception(e)
