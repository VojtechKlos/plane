# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import base64
import json

# Django imports
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

# Third party imports
from rest_framework import serializers, status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission, issue_hidden_from_guest
from plane.app.views.base import BaseAPIView
from plane.bgtasks.issue_activities_task import issue_activity
from plane.bgtasks.issue_description_version_task import issue_description_version_task
from plane.bgtasks.webhook_task import model_activity
from plane.db.models import Issue, ProjectMember, WorkspaceMember
from plane.utils.content_validator import validate_binary_data, validate_html_content
from plane.utils.host import base_host

from .publisher import LIVE_SOURCE_KEY, LIVE_SOURCE_VALUE


class RealtimeProjectsEndpoint(BaseAPIView):
    """Projects of a workspace whose work item events the user may receive (used by apps/live)."""

    def get(self, request, slug):
        project_ids = ProjectMember.objects.filter(
            workspace__slug=slug,
            member=request.user,
            is_active=True,
            project__archived_at__isnull=True,
        ).values_list("project_id", flat=True)
        return Response({"project_ids": [str(project_id) for project_id in project_ids]}, status=status.HTTP_200_OK)


class WorkItemDescriptionSerializer(serializers.Serializer):
    description_binary = serializers.CharField(required=True, allow_blank=False)
    description_html = serializers.CharField(required=True, allow_blank=True)
    description_json = serializers.JSONField(required=False, allow_null=True)

    def validate_description_binary(self, value):
        try:
            binary_data = base64.b64decode(value)
        except Exception:
            raise serializers.ValidationError("Failed to decode base64 data")
        is_valid, error_message = validate_binary_data(binary_data)
        if not is_valid:
            raise serializers.ValidationError(f"Invalid binary data: {error_message}")
        return binary_data

    def validate_description_html(self, value):
        if not value:
            return "<p></p>"
        is_valid, error_message, sanitized_html = validate_html_content(value)
        if not is_valid:
            raise serializers.ValidationError(error_message)
        return sanitized_html if sanitized_html is not None else value


class WorkItemDescriptionEndpoint(BaseAPIView):
    """Y.js state of a work item description, read and written by apps/live (collaborative editing)."""

    def _get_issue(self, slug, project_id, pk):
        return Issue.issue_objects.filter(workspace__slug=slug, project_id=project_id, pk=pk).first()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER, ROLE.GUEST])
    def get(self, request, slug, project_id, pk):
        if issue_hidden_from_guest(request, slug, project_id, pk):
            return Response({"error": "You don't have the required permissions."}, status=status.HTTP_403_FORBIDDEN)
        issue = self._get_issue(slug, project_id, pk)
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)
        binary_data = bytes(issue.description_binary) if issue.description_binary else b""
        return Response(
            {
                # live builds the Y.js document from the html when there is no binary yet, and otherwise
                # merges the html into it when the description was changed outside the editor meanwhile
                "description_binary": base64.b64encode(binary_data).decode() if binary_data else None,
                "description_html": issue.description_html or "<p></p>",
                # live opens the collaborative connection read-only when the user may not edit the work item
                "can_edit": self._can_edit(request, slug, project_id, issue),
            },
            status=status.HTTP_200_OK,
        )

    def _can_edit(self, request, slug, project_id, issue):
        """Mirror of the PATCH permission: admin/member of the project, a workspace admin in it, or the creator."""
        if issue.created_by_id == request.user.id:
            return True
        membership = ProjectMember.objects.filter(
            workspace__slug=slug, project_id=project_id, member=request.user, is_active=True
        ).first()
        if membership is None:
            return False
        if membership.role in (ROLE.ADMIN.value, ROLE.MEMBER.value):
            return True
        return WorkspaceMember.objects.filter(
            workspace__slug=slug, member=request.user, role=ROLE.ADMIN.value, is_active=True
        ).exists()

    @allow_permission([ROLE.ADMIN, ROLE.MEMBER], creator=True, model=Issue)
    def patch(self, request, slug, project_id, pk):
        issue = self._get_issue(slug, project_id, pk)
        if issue is None:
            return Response({"error": "Work item not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = WorkItemDescriptionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data

        old_description_html = issue.description_html
        html_changed = data["description_html"] != old_description_html

        issue.description_binary = data["description_binary"]
        update_fields = ["description_binary"]
        if html_changed:
            issue.description_html = data["description_html"]
            update_fields += ["description_html", "description_stripped", "updated_at", "updated_by"]
            if data.get("description_json") is not None:
                issue.description_json = data["description_json"]
                update_fields.append("description_json")
        issue.save(update_fields=update_fields)

        if html_changed:
            current_instance = json.dumps({"description_html": old_description_html}, cls=DjangoJSONEncoder)
            requested_data = {"description_html": issue.description_html}
            origin = base_host(request=request, is_app=True)
            issue_activity.delay(
                type="issue.activity.updated",
                requested_data=json.dumps({**requested_data, LIVE_SOURCE_KEY: LIVE_SOURCE_VALUE}),
                actor_id=str(request.user.id),
                issue_id=str(pk),
                project_id=str(project_id),
                current_instance=current_instance,
                epoch=int(timezone.now().timestamp()),
                notification=True,
                origin=origin,
            )
            model_activity.delay(
                model_name="issue",
                model_id=str(pk),
                requested_data=requested_data,
                current_instance=current_instance,
                actor_id=request.user.id,
                slug=slug,
                origin=origin,
            )
            issue_description_version_task.delay(
                updated_issue=current_instance,
                issue_id=str(pk),
                user_id=request.user.id,
            )
        return Response({"message": "Updated successfully"}, status=status.HTTP_200_OK)
