# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings
from django.core.management import BaseCommand, CommandError

# Module imports
from plane.db.models import Workspace
from plane.utils.project_auto_membership import ensure_project_memberships, missing_project_memberships


class Command(BaseCommand):
    help = (
        "Add every workspace member (above Guest) to every project they have no membership in, "
        "with the PROJECT_AUTO_MEMBERSHIP_ROLE project role. Runs whether or not PROJECT_AUTO_MEMBERSHIP is on."
    )

    def add_arguments(self, parser):
        parser.add_argument("--workspace", type=str, help="only this workspace slug")
        parser.add_argument("--dry-run", action="store_true", help="report what would change without writing")

    def handle(self, *args, **options):
        workspaces = Workspace.objects.all()
        slug = (options.get("workspace") or "").strip()
        if slug:
            workspaces = workspaces.filter(slug=slug)
            if not workspaces.exists():
                raise CommandError(f"Workspace '{slug}' does not exist")

        dry_run = options["dry_run"]
        self.stdout.write(f"Project role for new memberships: {settings.PROJECT_AUTO_MEMBERSHIP_ROLE}")

        total = 0
        for workspace in workspaces:
            if dry_run:
                count = len(missing_project_memberships(workspace.id))
            else:
                count = ensure_project_memberships(workspace.id)
            total += count
            self.stdout.write(f"{workspace.slug}: {count} membership(s) {'to add' if dry_run else 'added'}")

        self.stdout.write(self.style.SUCCESS(f"{'Would add' if dry_run else 'Added'} {total} membership(s)"))
