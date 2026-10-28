# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""PQL tokenizer/parser: grammar, precedence, limits and error positions."""

import pytest

from plane.utils.pql import MAX_CONDITIONS, PQLError, parse
from plane.utils.pql.parser import And, Comparison, FuncCall, Literal, Not, Or


@pytest.mark.unit
class TestPQLParser:
    def test_comparison(self):
        node = parse('priority = "high"')
        assert isinstance(node, Comparison)
        assert node.field.name == "priority"
        assert node.operator == "="
        assert node.values[0].value == "high"

    @pytest.mark.parametrize("operator", ["=", "!=", ">", ">=", "<", "<=", "~"])
    def test_comparison_operators(self, operator):
        assert parse(f'dueDate {operator} "2026-01-01"').operator == operator

    def test_precedence_not_and_or(self):
        node = parse('a = "1" OR b = "2" AND NOT c = "3"')
        assert isinstance(node, Or)
        assert isinstance(node.items[1], And)
        assert isinstance(node.items[1].items[1], Not)

    def test_parentheses_override_precedence(self):
        node = parse('(a = "1" OR b = "2") AND c = "3"')
        assert isinstance(node, And)
        assert isinstance(node.items[0], Or)

    def test_keywords_are_case_insensitive(self):
        node = parse('a = "1" and not b in ("x") or c is not null')
        assert isinstance(node, Or)

    def test_in_list_and_set_function(self):
        assert [v.value for v in parse('priority IN ("high", "urgent")').values] == ["high", "urgent"]
        node = parse("stateGroup NOT IN openStates()")
        assert node.operator == "not_in"
        assert isinstance(node.values[0], FuncCall)

    @pytest.mark.parametrize(
        "query, operator",
        [
            ("label IS NULL", "is_null"),
            ("label IS NOT NULL", "is_not_null"),
            ("label IS EMPTY", "is_empty"),
            ("label IS NOT EMPTY", "is_not_empty"),
        ],
    )
    def test_null_and_empty(self, query, operator):
        node = parse(query)
        assert node.operator == operator
        assert node.values == []

    def test_between_both_forms(self):
        for query in ("dueDate BETWEEN (daysAgo(7), today())", 'dueDate BETWEEN "2026-01-01" AND "2026-02-01"'):
            node = parse(query)
            assert node.operator == "between"
            assert len(node.values) == 2

    def test_between_and_is_not_a_logical_and(self):
        node = parse('dueDate BETWEEN "2026-01-01" AND "2026-02-01" AND priority = "low"')
        assert isinstance(node, And)
        assert len(node.items) == 2

    def test_literals(self):
        assert parse("isDraft = true").values[0].value is True
        assert parse("isDraft = FALSE").values[0].value is False
        assert parse('cf["p"] = 4.5').values[0].value == 4.5
        assert parse('cf["p"] >= -3').values[0].value == -3

    def test_string_escapes_and_single_quotes(self):
        assert parse(r'title ~ "say \"hi\""').values[0].value == 'say "hi"'
        assert parse("title ~ 'it'").values[0].value == "it"

    def test_standalone_function(self):
        node = parse('childOf("WEB-5")')
        assert isinstance(node, FuncCall)
        assert node.name == "childOf"
        assert isinstance(node.args[0], Literal)

    def test_custom_property_field(self):
        node = parse('cf["3f2c"] IS NULL')
        assert node.field.name == "cf"
        assert node.field.property_id == "3f2c"

    def test_condition_limit(self):
        ok = " AND ".join(f'f{i} = "x"' for i in range(MAX_CONDITIONS))
        parse(ok)
        with pytest.raises(PQLError, match="at most"):
            parse(ok + ' AND extra = "x"')

    def test_in_list_counts_as_one_condition(self):
        parse('a IN ("1","2","3") AND b = "1" AND c = "1" AND d = "1" AND e = "1"')

    def test_length_limit(self):
        with pytest.raises(PQLError, match="longer than"):
            parse('title ~ "' + "x" * 2100 + '"')

    @pytest.mark.parametrize(
        "query, message, column",
        [
            ("priority = high", 'Quote string values, e.g. "high"', 12),
            ('priority = "high" AND', "end of query", 22),
            ('title ~ "open', "Unterminated string", 9),
            ('priority == "x"', "Expected a value", 11),
            ('priority "x"', "Expected an operator", 10),
            ('(priority = "x"', "Expected ')'", 16),
            ('priority = "x" $', "Unexpected character", 16),
            ("label IS", "Expected NULL or EMPTY", 9),
        ],
    )
    def test_errors_report_position(self, query, message, column):
        with pytest.raises(PQLError) as exc_info:
            parse(query)
        assert message in str(exc_info.value)
        assert f"(at column {column})" in str(exc_info.value)

    @pytest.mark.parametrize("query", ["", "   ", None])
    def test_empty_query(self, query):
        with pytest.raises(PQLError, match="empty"):
            parse(query)
