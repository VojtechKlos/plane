# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
from types import SimpleNamespace

# Django imports
from django.db.models import Case, CharField, F, Func, Max, OuterRef, Subquery, Value, When

# Third party imports
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

# Module imports
from plane.db.models import CycleIssue, FileAsset, Issue, IssueLink
from plane.utils.filters import ComplexFilterBackend, IssueFilterSet
from plane.utils.pql import PQLError, compile_pql

# ComplexFilterBackend reads the allowed filters off the view it is given
FILTERS_VIEW = SimpleNamespace(filterset_class=IssueFilterSet)

PRIORITY_ORDER = ["urgent", "high", "medium", "low", "none"]
STATE_ORDER = ["backlog", "unstarted", "started", "completed", "cancelled"]


class InvalidWorkItemFilter(Exception):
    def __init__(self, body):
        self.body = body
        super().__init__(str(body))

    def response(self):
        return Response(self.body, status=status.HTTP_400_BAD_REQUEST)


class WorkItemFilters:
    """The `pql` and `filters` query parameters of a work item list request.

    Both are validated on construction, which raises InvalidWorkItemFilter.
    Querysets should start from ``base_queryset()``, since a PQL query about
    drafts, archived or intake work items needs rows ``Issue.issue_objects``
    hides, and then go through ``apply()``.
    """

    def __init__(self, request, slug):
        self.request = request
        self.compiled = None
        self.filters = request.GET.get("filters") or None

        pql = request.GET.get("pql", "").strip()
        if pql:
            try:
                self.compiled = compile_pql(pql, user_id=request.user.id, workspace_slug=slug)
            except PQLError as e:
                raise InvalidWorkItemFilter({"pql": str(e)})

        if self.filters:
            # Builds (and so validates) the filter tree without running a query
            self.apply_json_filters(Issue.objects.none())

    def base_queryset(self):
        if self.compiled is not None:
            return self.compiled.base_queryset()
        return Issue.issue_objects.all()

    def apply(self, queryset):
        if self.compiled is not None:
            queryset = queryset.filter(self.compiled.q)
        if self.filters:
            # The JSON filters join many-valued relations, so match them in a
            # subquery to keep each work item once in the outer queryset
            matching = self.apply_json_filters(Issue.objects.all())
            queryset = queryset.filter(pk__in=matching.values("pk"))
        return queryset

    def apply_json_filters(self, queryset):
        try:
            return ComplexFilterBackend().filter_queryset(
                self.request, queryset, FILTERS_VIEW, filter_data=self.filters
            )
        except DRFValidationError as e:
            detail = e.detail if isinstance(e.detail, dict) else {"message": e.detail}
            raise InvalidWorkItemFilter(
                {"filters": str(detail.get("message", detail)), "code": str(detail.get("code", "invalid_filter"))}
            )


def work_item_list_queryset(queryset):
    """``queryset`` with the annotations and related rows the work item list
    serializer reads."""
    return (
        queryset.annotate(
            sub_issues_count=Issue.issue_objects.filter(parent=OuterRef("id"))
            .order_by()
            .annotate(count=Func(F("id"), function="Count"))
            .values("count")
        )
        .annotate(
            cycle_id=Subquery(
                CycleIssue.objects.filter(issue=OuterRef("id"), deleted_at__isnull=True).values("cycle_id")[:1]
            )
        )
        .annotate(
            link_count=IssueLink.objects.filter(issue=OuterRef("id"))
            .order_by()
            .annotate(count=Func(F("id"), function="Count"))
            .values("count")
        )
        .annotate(
            attachment_count=FileAsset.objects.filter(
                issue_id=OuterRef("id"),
                entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            )
            .order_by()
            .annotate(count=Func(F("id"), function="Count"))
            .values("count")
        )
        .select_related("project")
        .select_related("workspace")
        .select_related("state")
        .select_related("parent")
        .prefetch_related("assignees")
        .prefetch_related("labels")
    )


def order_work_items(queryset, order_by_param):
    """Order by an already-sanitized ``order_by`` value, ranking priority and
    state groups by meaning rather than alphabetically."""
    # Priority Ordering
    if order_by_param == "priority" or order_by_param == "-priority":
        priority_order = PRIORITY_ORDER if order_by_param == "priority" else PRIORITY_ORDER[::-1]
        return queryset.annotate(
            priority_order=Case(
                *[When(priority=p, then=Value(i)) for i, p in enumerate(priority_order)],
                output_field=CharField(),
            )
        ).order_by("priority_order")

    # State Ordering
    if order_by_param in [
        "state__name",
        "state__group",
        "-state__name",
        "-state__group",
    ]:
        state_order = STATE_ORDER if order_by_param in ["state__name", "state__group"] else STATE_ORDER[::-1]
        return queryset.annotate(
            state_order=Case(
                *[When(state__group=state_group, then=Value(i)) for i, state_group in enumerate(state_order)],
                default=Value(len(state_order)),
                output_field=CharField(),
            )
        ).order_by("state_order")

    # assignee and label ordering
    if order_by_param in [
        "labels__name",
        "-labels__name",
        "assignees__first_name",
        "-assignees__first_name",
    ]:
        return queryset.annotate(
            max_values=Max(order_by_param[1::] if order_by_param.startswith("-") else order_by_param)
        ).order_by("-max_values" if order_by_param.startswith("-") else "max_values")

    return queryset.order_by(order_by_param)
