# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Compile a PQL AST into a Django ``Q`` over ``Issue``.

Many-valued relations (assignees, labels, cycles, modules, mentions,
subscribers, relations) compile to ``Exists()`` subqueries rather than joins:
ANDing two conditions on the same relation then means "has both", and NOT
means "has none", instead of Django reusing one join row for every condition.
"""

# Python imports
import calendar
import datetime
import uuid
from dataclasses import dataclass

# Django imports
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

# Module imports
from plane.db.models import (
    Cycle,
    CycleIssue,
    IntakeIssue,
    Issue,
    IssueAssignee,
    IssueLabel,
    IssueMention,
    IssueRelation,
    IssueSubscriber,
    ModuleIssue,
    ProjectMember,
    WorkspaceMember,
)
from plane.db.models.intake import IntakeIssueStatus
from plane.db.models.state import StateGroup

from .errors import PQLError
from .parser import And, Comparison, FuncCall, Literal, Not, Or, parse

PRIORITIES = ("urgent", "high", "medium", "low", "none")
STATE_GROUPS = ("backlog", "unstarted", "started", "completed", "cancelled")
OPEN_STATE_GROUPS = ("backlog", "unstarted", "started")
CLOSED_STATE_GROUPS = ("completed", "cancelled")
ACTIVE_STATE_GROUPS = ("unstarted", "started")

MAX_DATE_OFFSET = 10000

# PQL field -> (kind, details). Lookups are matched case-insensitively.
FIELDS = {
    "priority": ("enum", {"column": "priority", "choices": PRIORITIES}),
    "stateGroup": ("enum", {"column": "state__group", "choices": STATE_GROUPS}),
    "isDraft": ("flag", {"column": "is_draft"}),
    "isArchived": ("flag", {"column": "archived_at"}),
    "title": ("text", {"columns": ("name",), "exact": True}),
    "text": ("text", {"columns": ("name", "description_stripped"), "exact": False}),
    "id": ("identifier", {}),
    "dueDate": ("date", {"column": "target_date"}),
    "startDate": ("date", {"column": "start_date"}),
    "createdAt": ("datetime", {"column": "created_at"}),
    "updatedAt": ("datetime", {"column": "updated_at"}),
    "state": ("fk", {"column": "state_id", "values": "uuid"}),
    "project": ("fk", {"column": "project_id", "values": "uuid"}),
    "createdBy": ("fk", {"column": "created_by_id", "values": "user"}),
    "type": ("fk", {"column": "type_id", "values": "uuid"}),
    "assignee": (
        "relation",
        {"model": IssueAssignee, "column": "assignee_id", "values": "user", "extra": {}},
    ),
    "label": (
        "relation",
        {"model": IssueLabel, "column": "label_id", "values": "uuid", "extra": {"label__deleted_at__isnull": True}},
    ),
    "cycle": (
        "relation",
        {"model": CycleIssue, "column": "cycle_id", "values": "cycle", "extra": {"cycle__deleted_at__isnull": True}},
    ),
    "module": (
        "relation",
        {
            "model": ModuleIssue,
            "column": "module_id",
            "values": "uuid",
            "extra": {"module__deleted_at__isnull": True},
        },
    ),
    "mention": (
        "relation",
        {"model": IssueMention, "column": "mention_id", "values": "user", "extra": {}},
    ),
    "subscriber": (
        "relation",
        {"model": IssueSubscriber, "column": "subscriber_id", "values": "user", "extra": {}},
    ),
}
FIELD_LOOKUP = {name.lower(): name for name in FIELDS}

UNSUPPORTED_FIELDS = {"milestone", "teamspaceproject"}

DATE_FUNCTIONS = {
    "today",
    "now",
    "startofday",
    "endofday",
    "startofweek",
    "endofweek",
    "startofmonth",
    "endofmonth",
    "startofyear",
    "endofyear",
    "daysago",
    "daysfromnow",
    "weeksago",
    "weeksfromnow",
    "monthsago",
    "monthsfromnow",
}
USER_FUNCTIONS = {"currentuser", "membersof", "workspacemembers"}
CYCLE_FUNCTIONS = {"activecycle", "completedcycles", "upcomingcycles"}
STATE_GROUP_FUNCTIONS = {
    "openstates": OPEN_STATE_GROUPS,
    "closedstates": CLOSED_STATE_GROUPS,
    "activestates": ACTIVE_STATE_GROUPS,
}
RELATION_FUNCTIONS = {"childof", "parentof", "linkedto", "blockedby", "blocks", "duplicateof"}
PREDICATE_FUNCTIONS = {
    "isoverdue",
    "hasnoassignee",
    "hasnolabel",
    "istoplevel",
    "issubworkitem",
    "isepic",
    "haschildren",
    "hasstartandduedates",
    "isdraft",
    "isarchived",
    "isintake",
}

NULL_OPERATORS = {"is_null", "is_not_null", "is_empty", "is_not_empty"}


@dataclass
class CompiledPQL:
    q: Q
    include_drafts: bool = False
    include_archived: bool = False
    include_intake: bool = False

    def base_queryset(self):
        """The Issue queryset to filter with ``q``.

        ``Issue.issue_objects`` hides drafts, archived and triage (intake) work
        items, so a query that asks about any of them starts from
        ``Issue.objects`` and keeps only the other exclusions. Work items of
        archived projects stay hidden.
        """
        if not (self.include_drafts or self.include_archived or self.include_intake):
            return Issue.issue_objects.all()

        queryset = Issue.objects.filter(project__archived_at__isnull=True)
        if not self.include_intake:
            queryset = queryset.exclude(state__group=StateGroup.TRIAGE.value)
        if not self.include_archived:
            queryset = queryset.exclude(archived_at__isnull=False)
        if not self.include_drafts:
            queryset = queryset.exclude(is_draft=True)
        return queryset


class Compiler:
    def __init__(self, user_id, workspace_slug):
        self.user_id = user_id
        self.workspace_slug = workspace_slug
        self.today = timezone.localdate()
        self.now = timezone.now()
        self.include_drafts = False
        self.include_archived = False
        self.include_intake = False

    def compile(self, node):
        if isinstance(node, And):
            q = Q()
            for item in node.items:
                q &= self.compile(item)
            return q
        if isinstance(node, Or):
            q = self.compile(node.items[0])
            for item in node.items[1:]:
                q |= self.compile(item)
            return q
        if isinstance(node, Not):
            return ~self.compile(node.item)
        if isinstance(node, FuncCall):
            return self.compile_predicate(node)
        return self.compile_comparison(node)

    # ------------------------------------------------------------------
    # Comparisons
    # ------------------------------------------------------------------

    def compile_comparison(self, node: Comparison):
        field = node.field
        if field.property_id is not None:
            raise PQLError(
                "Custom properties (cf[...]) are not available in Plane Community Edition",
                field.position,
            )

        lowered = field.name.lower()
        if lowered in UNSUPPORTED_FIELDS:
            raise PQLError(f"Field '{field.name}' is not available in Plane Community Edition", field.position)
        if lowered not in FIELD_LOOKUP:
            raise PQLError(
                f"Unknown field '{field.name}'. Valid fields: {', '.join(sorted(FIELDS))}",
                field.position,
            )

        name = FIELD_LOOKUP[lowered]
        kind, details = FIELDS[name]
        return getattr(self, f"compile_{kind}")(name, details, node)

    def reject_operator(self, name, node, allowed):
        raise PQLError(
            f"Operator {self.describe_operator(node.operator)} is not supported for '{name}'. "
            f"Use one of: {', '.join(allowed)}",
            node.position,
        )

    @staticmethod
    def describe_operator(operator):
        return {
            "in": "IN",
            "not_in": "NOT IN",
            "between": "BETWEEN",
            "is_null": "IS NULL",
            "is_not_null": "IS NOT NULL",
            "is_empty": "IS EMPTY",
            "is_not_empty": "IS NOT EMPTY",
        }.get(operator, operator)

    def compile_enum(self, name, details, node):
        if node.operator not in ("=", "!=", "in", "not_in"):
            self.reject_operator(name, node, ["=", "!=", "IN", "NOT IN"])

        values = []
        for value in node.values:
            if isinstance(value, FuncCall) and value.name.lower() in STATE_GROUP_FUNCTIONS and name == "stateGroup":
                self.expect_args(value, 0)
                values.extend(STATE_GROUP_FUNCTIONS[value.name.lower()])
                continue
            text = self.expect_string(value, name).lower()
            if text not in details["choices"]:
                raise PQLError(
                    f"Invalid value \"{text}\" for '{name}'. Valid values: {', '.join(details['choices'])}",
                    value.position,
                )
            values.append(text)

        q = Q(**{f"{details['column']}__in": values})
        return ~q if node.operator in ("!=", "not_in") else q

    def compile_flag(self, name, details, node):
        if node.operator not in ("=", "!="):
            self.reject_operator(name, node, ["=", "!="])
        value = node.values[0]
        if not (isinstance(value, Literal) and isinstance(value.value, bool)):
            raise PQLError(f"'{name}' compares to true or false (unquoted)", value.position)

        if name == "isDraft":
            self.include_drafts = True
            q = Q(is_draft=True)
        else:
            self.include_archived = True
            q = Q(archived_at__isnull=False)

        wanted = value.value if node.operator == "=" else not value.value
        return q if wanted else ~q

    def compile_text(self, name, details, node):
        allowed = ["=", "!=", "~"] if details["exact"] else ["~"]
        if node.operator not in allowed:
            self.reject_operator(name, node, allowed)
        text = self.expect_string(node.values[0], name)

        if node.operator == "~":
            q = Q()
            for column in details["columns"]:
                q |= Q(**{f"{column}__icontains": text})
            return q

        q = Q(name__iexact=text)
        return ~q if node.operator == "!=" else q

    def compile_identifier(self, name, details, node):
        if node.operator not in ("=", "!=", "in", "not_in", "~"):
            self.reject_operator(name, node, ["=", "!=", "IN", "NOT IN", "~"])

        if node.operator == "~":
            text = self.expect_string(node.values[0], name).strip()
            reference = self.parse_work_item_reference(text)
            if reference is not None:
                return Q(**reference)
            if text.isdigit():
                return Q(sequence_id=int(text))
            return Q(project__identifier__icontains=text)

        q = Q(pk__in=[])
        for value in node.values:
            text = self.expect_string(value, name).strip()
            reference = self.parse_work_item_reference(text)
            if reference is None:
                raise PQLError(
                    f'Invalid work item id "{text}". Use the project identifier and number, e.g. "WEB-5"',
                    value.position,
                )
            q |= Q(**reference)
        return ~q if node.operator in ("!=", "not_in") else q

    def compile_date(self, name, details, node):
        return self.compile_temporal(name, details["column"], node, is_datetime=False)

    def compile_datetime(self, name, details, node):
        return self.compile_temporal(name, details["column"], node, is_datetime=True)

    def compile_temporal(self, name, column, node, is_datetime):
        operator = node.operator
        if operator in NULL_OPERATORS:
            q = Q(**{f"{column}__isnull": True})
            return q if operator in ("is_null", "is_empty") else ~q

        lookups = {"=": "exact", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
        if operator not in lookups and operator not in ("!=", "between"):
            self.reject_operator(name, node, ["=", "!=", ">", ">=", "<", "<=", "BETWEEN", "IS NULL", "IS NOT NULL"])

        def condition(lookup, value):
            moment = self.resolve_date(value, name)
            if isinstance(moment, datetime.datetime):
                if is_datetime:
                    return Q(**{f"{column}__{lookup}": moment})
                # A date column compares against the calendar day of a datetime value
                moment = timezone.localtime(moment).date()
                return Q(**{f"{column}__{lookup}": moment})
            if is_datetime:
                # A bare date means the whole day in the user's timezone
                return Q(**{f"{column}__date__{lookup}": moment})
            return Q(**{f"{column}__{lookup}": moment})

        if operator == "between":
            low, high = node.values
            return condition("gte", low) & condition("lte", high)
        if operator == "!=":
            return ~condition("exact", node.values[0])
        return condition(lookups[operator], node.values[0])

    def compile_fk(self, name, details, node):
        column = details["column"]
        operator = node.operator
        if operator in NULL_OPERATORS:
            q = Q(**{f"{column}__isnull": True})
            return q if operator in ("is_null", "is_empty") else ~q
        if operator not in ("=", "!=", "in", "not_in"):
            self.reject_operator(name, node, ["=", "!=", "IN", "NOT IN", "IS NULL", "IS NOT NULL"])

        q = self.membership_q(column, self.resolve_values(node.values, details["values"], name))
        return ~q if operator in ("!=", "not_in") else q

    def compile_relation(self, name, details, node):
        operator = node.operator
        related = details["model"].objects.filter(issue=OuterRef("pk"), deleted_at__isnull=True, **details["extra"])

        if operator in NULL_OPERATORS:
            q = Q(Exists(related))
            return ~q if operator in ("is_null", "is_empty") else q
        if operator not in ("=", "!=", "in", "not_in"):
            self.reject_operator(name, node, ["=", "!=", "IN", "NOT IN", "IS EMPTY", "IS NOT EMPTY"])

        match = self.membership_q(details["column"], self.resolve_values(node.values, details["values"], name))
        q = Q(Exists(related.filter(match)))
        return ~q if operator in ("!=", "not_in") else q

    @staticmethod
    def membership_q(column, values):
        """``column`` is one of ``values``: plain values plus value subqueries."""
        scalars = [value for value in values if not hasattr(value, "query")]
        subqueries = [value for value in values if hasattr(value, "query")]

        q = Q(**{f"{column}__in": scalars}) if scalars else Q(pk__in=[])
        for subquery in subqueries:
            q |= Q(**{f"{column}__in": subquery})
        return q

    # ------------------------------------------------------------------
    # Values
    # ------------------------------------------------------------------

    def resolve_values(self, values, kind, field_name):
        resolved = []
        for value in values:
            if isinstance(value, FuncCall):
                resolved.append(self.resolve_function_value(value, kind, field_name))
            else:
                resolved.append(self.expect_uuid(value, field_name))
        return resolved

    def resolve_function_value(self, call, kind, field_name):
        name = call.name.lower()
        slug = self.workspace_slug

        if kind == "user" and name in USER_FUNCTIONS:
            if name == "currentuser":
                self.expect_args(call, 0)
                return self.user_id
            if name == "workspacemembers":
                self.expect_args(call, 0)
                return WorkspaceMember.objects.filter(workspace__slug=slug, is_active=True).values("member_id")

            self.expect_args(call, 1)
            target = self.expect_string(call.args[0], call.name)
            scope, _, target_id = target.partition(":")
            if scope == "teamspace":
                raise PQLError("Teamspaces are not available in Plane Community Edition", call.position)
            if scope != "project":
                raise PQLError('membersOf() takes "project:<project-uuid>"', call.position)
            project_id = self.expect_uuid(Literal(target_id, call.args[0].position), call.name)
            return ProjectMember.objects.filter(workspace__slug=slug, project_id=project_id, is_active=True).values(
                "member_id"
            )

        if kind == "cycle" and name in CYCLE_FUNCTIONS:
            self.expect_args(call, 0)
            cycles = Cycle.objects.filter(workspace__slug=slug)
            if name == "activecycle":
                cycles = cycles.filter(start_date__lte=self.now, end_date__gte=self.now)
            elif name == "completedcycles":
                cycles = cycles.filter(end_date__lt=self.now)
            else:
                cycles = cycles.filter(start_date__gt=self.now)
            return cycles.values("id")

        raise PQLError(f"{call.name}() cannot be used as a value for '{field_name}'", call.position)

    def resolve_date(self, value, field_name):
        if isinstance(value, Literal):
            if not isinstance(value.value, str):
                raise PQLError(f"'{field_name}' compares to a quoted \"YYYY-MM-DD\" date", value.position)
            try:
                return datetime.date.fromisoformat(value.value)
            except ValueError:
                pass
            try:
                moment = datetime.datetime.fromisoformat(value.value)
            except ValueError:
                raise PQLError(
                    f'Invalid date "{value.value}" for \'{field_name}\'. Use "YYYY-MM-DD"',
                    value.position,
                )
            if timezone.is_naive(moment):
                moment = timezone.make_aware(moment)
            return moment

        name = value.name.lower()
        if name not in DATE_FUNCTIONS:
            raise PQLError(f"{value.name}() is not a date function", value.position)

        today = self.today
        if name in ("daysago", "daysfromnow", "weeksago", "weeksfromnow", "monthsago", "monthsfromnow"):
            self.expect_args(value, 1)
            amount = self.expect_count(value.args[0], value.name)
            sign = -1 if name.endswith("ago") else 1
            if name.startswith("days"):
                return today + datetime.timedelta(days=sign * amount)
            if name.startswith("weeks"):
                return today + datetime.timedelta(weeks=sign * amount)
            return self.shift_months(today, sign * amount)

        self.expect_args(value, 0)
        if name == "now":
            return self.now
        if name in ("today", "startofday", "endofday"):
            return today
        if name == "startofweek":
            return today - datetime.timedelta(days=today.weekday())
        if name == "endofweek":
            return today + datetime.timedelta(days=6 - today.weekday())
        if name == "startofmonth":
            return today.replace(day=1)
        if name == "endofmonth":
            return today.replace(day=calendar.monthrange(today.year, today.month)[1])
        if name == "startofyear":
            return today.replace(month=1, day=1)
        return today.replace(month=12, day=31)

    @staticmethod
    def shift_months(day, months):
        month_index = day.month - 1 + months
        year = day.year + month_index // 12
        month = month_index % 12 + 1
        return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))

    def expect_args(self, call, count):
        if len(call.args) != count:
            plural = "argument" if count == 1 else "arguments"
            raise PQLError(f"{call.name}() takes {count} {plural}, got {len(call.args)}", call.position)

    @staticmethod
    def expect_string(value, context):
        if not (isinstance(value, Literal) and isinstance(value.value, str)):
            raise PQLError(f"'{context}' expects a quoted string value", value.position)
        return value.value

    def expect_uuid(self, value, field_name):
        text = self.expect_string(value, field_name)
        try:
            return str(uuid.UUID(text))
        except ValueError:
            raise PQLError(f"'{field_name}' expects a UUID, got \"{text}\"", value.position)

    @staticmethod
    def expect_count(value, function_name):
        if not (
            isinstance(value, Literal)
            and isinstance(value.value, int)
            and not isinstance(value.value, bool)
            and 0 <= value.value <= MAX_DATE_OFFSET
        ):
            raise PQLError(
                f"{function_name}() takes a whole number between 0 and {MAX_DATE_OFFSET}",
                value.position,
            )
        return value.value

    def parse_work_item_reference(self, text):
        """Lookups selecting the work item "WEB-5" (or a UUID) in this
        workspace, or None when ``text`` is neither."""
        try:
            return {"pk": str(uuid.UUID(text)), "workspace__slug": self.workspace_slug}
        except ValueError:
            pass

        identifier, _, sequence = text.rpartition("-")
        if not identifier or not sequence.isdigit():
            return None
        return {
            "project__identifier": identifier.upper(),
            "sequence_id": int(sequence),
            "workspace__slug": self.workspace_slug,
        }

    # ------------------------------------------------------------------
    # Standalone functions
    # ------------------------------------------------------------------

    def compile_predicate(self, call: FuncCall):
        name = call.name.lower()

        if name in RELATION_FUNCTIONS:
            return self.compile_relation_function(call, name)

        if name not in PREDICATE_FUNCTIONS:
            if name in DATE_FUNCTIONS | USER_FUNCTIONS | CYCLE_FUNCTIONS | set(STATE_GROUP_FUNCTIONS):
                raise PQLError(
                    f"{call.name}() is a value; compare a field to it, e.g. dueDate < today()",
                    call.position,
                )
            raise PQLError(f"Unknown function {call.name}()", call.position)

        self.expect_args(call, 0)

        if name == "isoverdue":
            return Q(target_date__lt=self.today, state__group__in=OPEN_STATE_GROUPS)
        if name == "hasnoassignee":
            return ~Q(Exists(IssueAssignee.objects.filter(issue=OuterRef("pk"), deleted_at__isnull=True)))
        if name == "hasnolabel":
            return ~Q(
                Exists(
                    IssueLabel.objects.filter(
                        issue=OuterRef("pk"), deleted_at__isnull=True, label__deleted_at__isnull=True
                    )
                )
            )
        if name == "istoplevel":
            return Q(parent__isnull=True)
        if name == "issubworkitem":
            return Q(parent__isnull=False)
        if name == "isepic":
            return Q(type__is_epic=True)
        if name == "haschildren":
            return Q(Exists(Issue.issue_objects.filter(parent=OuterRef("pk"))))
        if name == "hasstartandduedates":
            return Q(start_date__isnull=False, target_date__isnull=False)
        if name == "isdraft":
            self.include_drafts = True
            return Q(is_draft=True)
        if name == "isarchived":
            self.include_archived = True
            return Q(archived_at__isnull=False)

        # isintake
        self.include_intake = True
        return Q(
            Exists(
                IntakeIssue.objects.filter(issue=OuterRef("pk"), deleted_at__isnull=True).exclude(
                    status=IntakeIssueStatus.ACCEPTED
                )
            )
        )

    def compile_relation_function(self, call, name):
        self.expect_args(call, 1)
        text = self.expect_string(call.args[0], call.name).strip()
        reference = self.parse_work_item_reference(text)
        if reference is None:
            raise PQLError(
                f'{call.name}() takes a work item id like "WEB-5" or a UUID, got "{text}"',
                call.args[0].position,
            )

        def prefixed(prefix):
            return {f"{prefix}__{key}": value for key, value in reference.items()}

        if name == "childof":
            return Q(**prefixed("parent"))
        if name == "parentof":
            return Q(Exists(Issue.objects.filter(parent=OuterRef("pk"), **reference)))

        relations = IssueRelation.objects.filter(deleted_at__isnull=True)
        if name == "blockedby":
            # A row (issue=X, related_issue=Y, blocked_by) reads "X is blocked by Y"
            return Q(
                Exists(relations.filter(relation_type="blocked_by", issue=OuterRef("pk"), **prefixed("related_issue")))
            )
        if name == "blocks":
            return Q(
                Exists(relations.filter(relation_type="blocked_by", related_issue=OuterRef("pk"), **prefixed("issue")))
            )

        relation_type = "relates_to" if name == "linkedto" else "duplicate"
        return Q(
            Exists(relations.filter(relation_type=relation_type, issue=OuterRef("pk"), **prefixed("related_issue")))
        ) | Q(Exists(relations.filter(relation_type=relation_type, related_issue=OuterRef("pk"), **prefixed("issue"))))


def compile_pql(text, *, user_id, workspace_slug):
    """Parse and compile ``text``. Raises PQLError for invalid queries."""
    compiler = Compiler(user_id=user_id, workspace_slug=workspace_slug)
    q = compiler.compile(parse(text))
    return CompiledPQL(
        q=q,
        include_drafts=compiler.include_drafts,
        include_archived=compiler.include_archived,
        include_intake=compiler.include_intake,
    )
