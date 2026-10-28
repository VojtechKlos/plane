# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db.models import Count, F, FilteredRelation, Q

# Third party imports
from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse, extend_schema

# Module imports
from plane.api.serializers import IssueSerializer
from plane.app.permissions import ProjectEntityPermission, WorkspaceViewerPermission
from plane.db.models import Issue
from plane.utils.openapi import (
    CURSOR_PARAMETER,
    EXPAND_PARAMETER,
    FIELDS_PARAMETER,
    FILTERS_PARAMETER,
    GROUP_BY_PARAMETER,
    INVALID_REQUEST_RESPONSE,
    ORDER_BY_PARAMETER,
    PER_PAGE_PARAMETER,
    PQL_PARAMETER,
    SUB_GROUP_BY_PARAMETER,
    WORKSPACE_SLUG_PARAMETER,
    create_paginated_response,
    work_item_docs,
)
from plane.utils.order_queryset import ISSUE_ORDER_BY_ALLOWLIST, sanitize_order_by
from .base import BaseAPIView
from .issue import is_restricted_guest, visible_issue_q
from .work_item_filters import (
    InvalidWorkItemFilter,
    WorkItemFilters,
    order_work_items,
    work_item_list_queryset,
)

# Count group keys (the names plane-sdk sends) -> Issue column
GROUP_BY_COLUMNS = {
    "state_id": "state_id",
    "state__group": "state__group",
    "priority": "priority",
    "project_id": "project_id",
    "type_id": "type_id",
    "created_by": "created_by_id",
    "target_date": "target_date",
    "start_date": "start_date",
}
# Many-valued count group keys -> (soft-deleted relation, its column)
GROUP_BY_RELATIONS = {
    "labels__id": ("label_issue", "label_id"),
    "assignees__id": ("issue_assignee", "assignee_id"),
    "issue_module__module_id": ("issue_module", "module_id"),
    "cycle_id": ("issue_cycle", "cycle_id"),
}
# Group keys of commercial editions
UNAVAILABLE_GROUP_BY = {"release_work_items__release_id", "milestone_id"}


def visible_work_items(request, slug, work_item_filters):
    """The workspace's work items the caller may view, narrowed by pql/filters."""
    return work_item_filters.apply(
        work_item_filters.base_queryset().filter(workspace__slug=slug).filter(visible_issue_q(request.user))
    )


def annotate_group(queryset, group_by, alias):
    """Annotate ``alias`` with the ``group_by`` value. A work item with several
    labels (or assignees, ...) counts once in each group; one with none gets
    None."""
    if group_by in GROUP_BY_RELATIONS:
        relation, column = GROUP_BY_RELATIONS[group_by]
        queryset = queryset.annotate(
            **{
                f"{alias}_relation": FilteredRelation(
                    relation, condition=Q(**{f"{relation}__deleted_at__isnull": True})
                )
            }
        )
        return queryset.annotate(**{alias: F(f"{alias}_relation__{column}")})
    return queryset.annotate(**{alias: F(GROUP_BY_COLUMNS[group_by])})


def group_key(value):
    return "None" if value is None else str(value)


class WorkspaceWorkItemListAPIEndpoint(BaseAPIView):
    """List work items across every project of the workspace the caller can view."""

    model = Issue
    permission_classes = [WorkspaceViewerPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="list_workspace_work_items",
        summary="List workspace work items",
        description=(
            "Retrieve a paginated list of the work items the caller can view across all projects of the "
            "workspace, optionally narrowed with `pql` or `filters`."
        ),
        tags=["Work Items"],
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            CURSOR_PARAMETER,
            PER_PAGE_PARAMETER,
            ORDER_BY_PARAMETER,
            PQL_PARAMETER,
            FILTERS_PARAMETER,
            FIELDS_PARAMETER,
            EXPAND_PARAMETER,
        ],
        responses={
            200: create_paginated_response(
                IssueSerializer,
                "PaginatedWorkspaceWorkItemResponse",
                "Paginated list of workspace work items",
                "Paginated Workspace Work Items",
            ),
            400: INVALID_REQUEST_RESPONSE,
        },
    )
    def get(self, request, slug):
        try:
            work_item_filters = WorkItemFilters(request, slug)
        except InvalidWorkItemFilter as exc:
            return exc.response()

        order_by_param = sanitize_order_by(
            request.GET.get("order_by", "-created_at"),
            ISSUE_ORDER_BY_ALLOWLIST,
            default="-created_at",
        )

        work_items = visible_work_items(request, slug, work_item_filters).distinct()

        return self.paginate(
            request=request,
            queryset=order_work_items(work_item_list_queryset(work_items), order_by_param),
            total_count_queryset=work_items,
            on_results=lambda issues: IssueSerializer(issues, many=True, fields=self.fields, expand=self.expand).data,
        )


class WorkspaceWorkItemCountAPIEndpoint(BaseAPIView):
    """Count the work items the caller can view, optionally grouped."""

    model = Issue
    permission_classes = [WorkspaceViewerPermission]
    use_read_replica = True

    @extend_schema(
        operation_id="count_workspace_work_items",
        summary="Count workspace work items",
        description=(
            "Count the work items the caller can view across the workspace, optionally narrowed with `pql` "
            "or `filters` and grouped by up to two fields."
        ),
        tags=["Work Items"],
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            PQL_PARAMETER,
            FILTERS_PARAMETER,
            GROUP_BY_PARAMETER,
            SUB_GROUP_BY_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(description="Work item counts"),
            400: INVALID_REQUEST_RESPONSE,
        },
    )
    def get(self, request, slug):
        group_by = request.GET.get("group_by") or None
        sub_group_by = request.GET.get("sub_group_by") or None

        for param, value in (("group_by", group_by), ("sub_group_by", sub_group_by)):
            if value is None:
                continue
            if value in UNAVAILABLE_GROUP_BY:
                return Response(
                    {param: f"Grouping by '{value}' is not available in Plane Community Edition"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if value not in GROUP_BY_COLUMNS and value not in GROUP_BY_RELATIONS:
                valid = ", ".join([*GROUP_BY_COLUMNS, *GROUP_BY_RELATIONS])
                return Response(
                    {param: f"Invalid value '{value}'. Valid values: {valid}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if sub_group_by and not group_by:
            return Response(
                {"sub_group_by": "sub_group_by can only be used together with group_by"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            work_item_filters = WorkItemFilters(request, slug)
        except InvalidWorkItemFilter as exc:
            return exc.response()

        work_items = visible_work_items(request, slug, work_item_filters)
        total_count = work_items.aggregate(count=Count("id", distinct=True))["count"]

        grouped_counts = {}
        if group_by:
            grouped = annotate_group(work_items, group_by, "count_group")
            for row in grouped.order_by().values("count_group").annotate(count=Count("id", distinct=True)):
                grouped_counts[group_key(row["count_group"])] = {"count": row["count"]}

            if sub_group_by:
                for counts in grouped_counts.values():
                    counts["sub_grouped_counts"] = {}
                sub_grouped = annotate_group(grouped, sub_group_by, "count_sub_group")
                rows = (
                    sub_grouped.order_by()
                    .values("count_group", "count_sub_group")
                    .annotate(count=Count("id", distinct=True))
                )
                for row in rows:
                    group = grouped_counts[group_key(row["count_group"])]
                    group["sub_grouped_counts"][group_key(row["count_sub_group"])] = {"count": row["count"]}

        return Response(
            {
                "grouped_by": group_by,
                "sub_grouped_by": sub_group_by,
                "total_count": total_count,
                "grouped_counts": grouped_counts,
            },
            status=status.HTTP_200_OK,
        )


class ArchivedWorkItemListAPIEndpoint(BaseAPIView):
    """List the archived work items of a project."""

    model = Issue
    permission_classes = [ProjectEntityPermission]
    use_read_replica = True

    @work_item_docs(
        operation_id="list_archived_work_items",
        summary="List archived work items",
        description="Retrieve a paginated list of the archived work items in a project.",
        parameters=[
            CURSOR_PARAMETER,
            PER_PAGE_PARAMETER,
            ORDER_BY_PARAMETER,
            PQL_PARAMETER,
            FILTERS_PARAMETER,
            FIELDS_PARAMETER,
            EXPAND_PARAMETER,
        ],
        responses={
            200: create_paginated_response(
                IssueSerializer,
                "PaginatedArchivedWorkItemResponse",
                "Paginated list of archived work items",
                "Paginated Archived Work Items",
            ),
            400: INVALID_REQUEST_RESPONSE,
        },
    )
    def get(self, request, slug, project_id):
        try:
            work_item_filters = WorkItemFilters(request, slug)
        except InvalidWorkItemFilter as exc:
            return exc.response()

        order_by_param = sanitize_order_by(
            request.GET.get("order_by", "-archived_at"),
            ISSUE_ORDER_BY_ALLOWLIST,
            default="-archived_at",
        )

        work_items = work_item_filters.apply(
            Issue.objects.filter(workspace__slug=slug, project_id=project_id, archived_at__isnull=False)
        ).distinct()

        # Restricted guests may only see work items they created
        if is_restricted_guest(request.user.id, project_id, slug):
            work_items = work_items.filter(created_by=request.user)

        return self.paginate(
            request=request,
            queryset=order_work_items(work_item_list_queryset(work_items), order_by_param),
            total_count_queryset=work_items,
            on_results=lambda issues: IssueSerializer(issues, many=True, fields=self.fields, expand=self.expand).data,
        )
