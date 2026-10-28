# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third Party imports
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    extend_schema,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiRequest,
)

# Module imports
from .base import BaseAPIView
from plane.api.serializers import (
    UserLiteSerializer,
    ProjectMemberSerializer,
    WorkspaceMemberLiteAPISerializer,
    ProjectMemberLiteAPISerializer,
)
from plane.db.models import User, Workspace, WorkspaceMember, Project, ProjectMember
from plane.api.serializers.member import MEMBER_ROLE_SLUGS
from plane.utils.order_queryset import MEMBER_ORDER_BY_ALLOWLIST, sanitize_order_by
from plane.utils.permissions import ProjectMemberPermission, WorkSpaceAdminPermission, ProjectAdminPermission
from plane.utils.openapi import (
    WORKSPACE_SLUG_PARAMETER,
    PROJECT_ID_PARAMETER,
    CURSOR_PARAMETER,
    PER_PAGE_PARAMETER,
    UNAUTHORIZED_RESPONSE,
    FORBIDDEN_RESPONSE,
    WORKSPACE_NOT_FOUND_RESPONSE,
    PROJECT_NOT_FOUND_RESPONSE,
    WORKSPACE_MEMBER_EXAMPLE,
    PROJECT_MEMBER_EXAMPLE,
    create_paginated_response,
)


MEMBER_TEXT_FILTERS = ("first_name", "last_name", "email", "display_name")
ROLES_BY_SLUG = {slug: role for role, slug in MEMBER_ROLE_SLUGS.items()}

# The member-lite filters plane-sdk sends (MemberListQueryParams)
MEMBER_LITE_FILTER_PARAMETERS = [
    *[
        OpenApiParameter(
            name=field,
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=False,
            description=f"Members whose {field.replace('_', ' ')} contains this text, ignoring case",
        )
        for field in MEMBER_TEXT_FILTERS
    ],
    OpenApiParameter(
        name="role_slug",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        enum=list(ROLES_BY_SLUG),
        description="Members with this role",
    ),
    OpenApiParameter(
        name="is_active",
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Active (true) or deactivated (false) memberships",
    ),
    OpenApiParameter(
        name="is_bot",
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Bot (true) or human (false) accounts",
    ),
    OpenApiParameter(
        name="order_by",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description=f"One of {', '.join(sorted(MEMBER_ORDER_BY_ALLOWLIST))}; prefix with '-' for descending",
    ),
]


def bool_query_param(request, name):
    value = request.GET.get(name)
    if not value:
        return None
    if value.lower() in ("true", "1"):
        return True
    if value.lower() in ("false", "0"):
        return False
    raise ValidationError({name: f"'{value}' is not a boolean. Use true or false"})


def filter_lite_members(request, queryset):
    """Apply the member-lite filters and ordering to a WorkspaceMember or
    ProjectMember queryset."""
    for field in MEMBER_TEXT_FILTERS:
        value = request.GET.get(field)
        if value:
            queryset = queryset.filter(**{f"member__{field}__icontains": value})

    role_slug = request.GET.get("role_slug")
    if role_slug:
        if role_slug not in ROLES_BY_SLUG:
            raise ValidationError(
                {"role_slug": f"Invalid role slug '{role_slug}'. Valid values: {', '.join(ROLES_BY_SLUG)}"}
            )
        queryset = queryset.filter(role=ROLES_BY_SLUG[role_slug])

    is_active = bool_query_param(request, "is_active")
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)

    is_bot = bool_query_param(request, "is_bot")
    if is_bot is not None:
        queryset = queryset.filter(member__is_bot=is_bot)

    order_by = sanitize_order_by(request.GET.get("order_by"), MEMBER_ORDER_BY_ALLOWLIST, default="-created_at")
    field = order_by.lstrip("-")
    if field in MEMBER_TEXT_FILTERS:
        order_by = order_by.replace(field, f"member__{field}")
    return queryset.order_by(order_by)


class WorkspaceMemberAPIEndpoint(BaseAPIView):
    permission_classes = [WorkSpaceAdminPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="get_workspace_members",
        summary="List workspace members",
        description="Retrieve all users who are members of the specified workspace.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER],
        responses={
            200: OpenApiResponse(
                description="List of workspace members with their roles",
                response={
                    "type": "array",
                    "items": {
                        "allOf": [
                            {"$ref": "#/components/schemas/UserLite"},
                            {
                                "type": "object",
                                "properties": {
                                    "role": {
                                        "type": "integer",
                                        "description": "Member role in the workspace",
                                    }
                                },
                            },
                        ]
                    },
                },
                examples=[WORKSPACE_MEMBER_EXAMPLE],
            ),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    # Get all the users that are present inside the workspace
    def get(self, request, slug):
        """List workspace members

        Retrieve all users who are members of the specified workspace.
        Returns user profiles with their respective workspace roles and permissions.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace_members = WorkspaceMember.objects.filter(workspace__slug=slug).select_related("member")

        # Get all the users with their roles
        users_with_roles = []
        for workspace_member in workspace_members:
            user_data = UserLiteSerializer(workspace_member.member).data
            user_data["role"] = workspace_member.role
            users_with_roles.append(user_data)

        return Response(users_with_roles, status=status.HTTP_200_OK)


class ProjectMemberListCreateAPIEndpoint(BaseAPIView):
    permission_classes = [ProjectMemberPermission]
    use_read_replica = True

    def get_permissions(self):
        if self.request.method == "GET":
            return [ProjectMemberPermission()]
        return [ProjectAdminPermission()]

    @extend_schema(
        operation_id="get_project_members",
        summary="List project members",
        description="Retrieve all users who are members of the specified project.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                description="List of project members with their roles",
                response=UserLiteSerializer,
                examples=[PROJECT_MEMBER_EXAMPLE],
            ),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: PROJECT_NOT_FOUND_RESPONSE,
        },
    )
    # Get all the users that are present inside the workspace
    def get(self, request, slug, project_id):
        """List project members

        Retrieve all users who are members of the specified project.
        Returns user profiles with their project-specific roles and access levels.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the workspace members that are present inside the workspace
        project_members = ProjectMember.objects.filter(project_id=project_id, workspace__slug=slug).values_list(
            "member_id", flat=True
        )

        # Get all the users that are present inside the workspace
        users = UserLiteSerializer(User.objects.filter(id__in=project_members), many=True).data
        return Response(users, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="create_project_member",
        summary="Create project member",
        description="Create a new project member",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={201: OpenApiResponse(description="Project member created", response=ProjectMemberSerializer)},
        request=OpenApiRequest(request=ProjectMemberSerializer),
    )
    def post(self, request, slug, project_id):
        serializer = ProjectMemberSerializer(data=request.data, context={"slug": slug})
        serializer.is_valid(raise_exception=True)
        serializer.save(project_id=project_id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# API endpoint to get and update a project member
class ProjectMemberDetailAPIEndpoint(ProjectMemberListCreateAPIEndpoint):
    @extend_schema(
        operation_id="get_project_member",
        summary="Get project member",
        description="Retrieve a project member by ID.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={
            200: OpenApiResponse(description="Project member", response=ProjectMemberSerializer),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: PROJECT_NOT_FOUND_RESPONSE,
        },
    )
    # Get a project member by ID
    def get(self, request, slug, project_id, pk):
        """Get project member

        Retrieve a project member by ID.
        Returns a project member with their project-specific roles and access levels.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the workspace members that are present inside the workspace
        project_members = ProjectMember.objects.get(project_id=project_id, workspace__slug=slug, pk=pk)
        user = User.objects.get(id=project_members.member_id)
        user = UserLiteSerializer(user).data
        return Response(user, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="update_project_member",
        summary="Update project member",
        description="Update a project member",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={200: OpenApiResponse(description="Project member updated", response=ProjectMemberSerializer)},
        request=OpenApiRequest(request=ProjectMemberSerializer),
    )
    def patch(self, request, slug, project_id, pk):
        project_member = ProjectMember.objects.get(project_id=project_id, workspace__slug=slug, pk=pk)
        # A partial update may omit `member`, so check the existing target's workspace membership here.
        if not WorkspaceMember.objects.filter(
            workspace__slug=slug, member_id=project_member.member_id, is_active=True
        ).exists():
            return Response({"member": ["Member not found in workspace"]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ProjectMemberSerializer(project_member, data=request.data, partial=True, context={"slug": slug})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="delete_project_member",
        summary="Delete project member",
        description="Delete a project member",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        responses={204: OpenApiResponse(description="Project member deleted")},
    )
    def delete(self, request, slug, project_id, pk):
        project_member = ProjectMember.objects.get(project_id=project_id, workspace__slug=slug, pk=pk)
        project_member.is_active = False
        project_member.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceMemberLiteAPIEndpoint(BaseAPIView):
    """Workspace members (lite) list endpoint."""

    permission_classes = [WorkSpaceAdminPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="get_workspace_members_lite",
        summary="List workspace members (lite)",
        description="Retrieve a paginated, lightweight list of workspace members for pickers and directories.",
        tags=["Members"],
        parameters=[WORKSPACE_SLUG_PARAMETER, CURSOR_PARAMETER, PER_PAGE_PARAMETER, *MEMBER_LITE_FILTER_PARAMETERS],
        responses={
            200: create_paginated_response(
                WorkspaceMemberLiteAPISerializer,
                "PaginatedWorkspaceMemberLite",
                "Paginated list of workspace members with minimal fields",
                "Paginated Workspace Members (Lite)",
            ),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: WORKSPACE_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug):
        """List workspace members (lite)

        Retrieve a paginated, lightweight list of workspace members, optimized for
        pickers and directories.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        workspace_members = filter_lite_members(
            request, WorkspaceMember.objects.filter(workspace__slug=slug).select_related("member")
        )
        return self.paginate(
            request=request,
            queryset=workspace_members,
            on_results=lambda members: WorkspaceMemberLiteAPISerializer(members, many=True).data,
        )


class ProjectMemberLiteAPIEndpoint(BaseAPIView):
    """Project members (lite) list endpoint."""

    permission_classes = [ProjectMemberPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="get_project_members_lite",
        summary="List project members (lite)",
        description="Retrieve a paginated, lightweight list of project members for pickers and directories.",
        tags=["Members"],
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            PROJECT_ID_PARAMETER,
            CURSOR_PARAMETER,
            PER_PAGE_PARAMETER,
            *MEMBER_LITE_FILTER_PARAMETERS,
        ],
        responses={
            200: create_paginated_response(
                ProjectMemberLiteAPISerializer,
                "PaginatedProjectMemberLite",
                "Paginated list of project members with minimal fields",
                "Paginated Project Members (Lite)",
            ),
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: PROJECT_NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug, project_id):
        """List project members (lite)

        Retrieve a paginated, lightweight list of project members, optimized for
        pickers and directories.
        """
        # Check if the workspace exists
        if not Workspace.objects.filter(slug=slug).exists():
            return Response(
                {"error": "Provided workspace does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not Project.objects.filter(id=project_id, workspace__slug=slug).exists():
            return Response(
                {"error": "Provided project does not exist"},
                status=status.HTTP_404_NOT_FOUND,
            )

        project_members = filter_lite_members(
            request, ProjectMember.objects.filter(project_id=project_id, workspace__slug=slug).select_related("member")
        )
        return self.paginate(
            request=request,
            queryset=project_members,
            on_results=lambda members: ProjectMemberLiteAPISerializer(members, many=True).data,
        )
