# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Tokenizer and recursive-descent parser for PQL (Plane Query Language).

Grammar (keywords are case-insensitive; precedence NOT > AND > OR)::

    query      := or_expr EOF
    or_expr    := and_expr (OR and_expr)*
    and_expr   := not_expr (AND not_expr)*
    not_expr   := NOT not_expr | primary
    primary    := "(" or_expr ")" | func_call | comparison
    comparison := field ( cmp_op value
                        | [NOT] IN ( "(" value ("," value)* ")" | func_call )
                        | IS [NOT] (NULL | EMPTY)
                        | BETWEEN ( "(" value "," value ")" | value AND value ) )
    field      := IDENT | "cf" "[" STRING "]"
    cmp_op     := "=" | "!=" | ">" | ">=" | "<" | "<=" | "~"
    value      := STRING | NUMBER | TRUE | FALSE | func_call
    func_call  := IDENT "(" [value ("," value)*] ")"
"""

# Python imports
from dataclasses import dataclass, field as dataclass_field
from typing import Optional, Union

# Module imports
from .errors import PQLError

MAX_QUERY_LENGTH = 2000
MAX_CONDITIONS = 5
MAX_LIST_ITEMS = 100

KEYWORDS = {"AND", "OR", "NOT", "IN", "IS", "NULL", "EMPTY", "BETWEEN", "TRUE", "FALSE"}
COMPARISON_OPERATORS = {"=", "!=", ">", ">=", "<", "<=", "~"}


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass
class Literal:
    value: Union[str, int, float, bool]
    position: int


@dataclass
class FuncCall:
    name: str
    args: list
    position: int


Value = Union[Literal, FuncCall]


@dataclass
class FieldRef:
    name: str
    position: int
    # Set for custom-property references: cf["<uuid>"]
    property_id: Optional[str] = None


@dataclass
class Comparison:
    field: FieldRef
    # One of COMPARISON_OPERATORS, or "in", "not_in", "between",
    # "is_null", "is_not_null", "is_empty", "is_not_empty"
    operator: str
    values: list = dataclass_field(default_factory=list)
    position: int = 0


@dataclass
class And:
    items: list


@dataclass
class Or:
    items: list


@dataclass
class Not:
    item: object


Node = Union[And, Or, Not, Comparison, FuncCall]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


@dataclass
class Token:
    kind: str  # "IDENT", "KEYWORD", "STRING", "NUMBER", "OP", "PUNCT", "EOF"
    value: object
    position: int


def tokenize(text):
    tokens = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char.isspace():
            index += 1
            continue

        if char in "\"'":
            quote = char
            start = index
            index += 1
            chars = []
            while True:
                if index >= length:
                    raise PQLError("Unterminated string", start)
                current = text[index]
                if current == "\\" and index + 1 < length:
                    chars.append(text[index + 1])
                    index += 2
                    continue
                if current == quote:
                    index += 1
                    break
                chars.append(current)
                index += 1
            tokens.append(Token("STRING", "".join(chars), start))
            continue

        if char.isdigit() or (char == "-" and index + 1 < length and text[index + 1].isdigit()):
            start = index
            index += 1
            while index < length and (text[index].isdigit() or text[index] == "."):
                index += 1
            raw = text[start:index]
            try:
                number = float(raw) if "." in raw else int(raw)
            except ValueError:
                raise PQLError(f"Invalid number '{raw}'", start)
            tokens.append(Token("NUMBER", number, start))
            continue

        if char.isalpha() or char == "_":
            start = index
            while index < length and (text[index].isalnum() or text[index] == "_"):
                index += 1
            word = text[start:index]
            if word.upper() in KEYWORDS:
                tokens.append(Token("KEYWORD", word.upper(), start))
            else:
                tokens.append(Token("IDENT", word, start))
            continue

        two_chars = text[index : index + 2]
        if two_chars in (">=", "<=", "!="):
            tokens.append(Token("OP", two_chars, index))
            index += 2
            continue

        if char in "=<>~":
            tokens.append(Token("OP", char, index))
            index += 1
            continue

        if char in "(),[]":
            tokens.append(Token("PUNCT", char, index))
            index += 1
            continue

        raise PQLError(f"Unexpected character '{char}'", index)

    tokens.append(Token("EOF", None, length))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class Parser:
    def __init__(self, text):
        self.tokens = tokenize(text)
        self.index = 0

    # Token helpers

    @property
    def current(self):
        return self.tokens[self.index]

    def advance(self):
        token = self.tokens[self.index]
        self.index += 1
        return token

    def is_keyword(self, *words):
        return self.current.kind == "KEYWORD" and self.current.value in words

    def is_punct(self, char):
        return self.current.kind == "PUNCT" and self.current.value == char

    def expect_punct(self, char):
        if not self.is_punct(char):
            raise PQLError(f"Expected '{char}' but found {self.describe(self.current)}", self.current.position)
        return self.advance()

    def expect_keyword(self, word):
        if not self.is_keyword(word):
            raise PQLError(f"Expected {word} but found {self.describe(self.current)}", self.current.position)
        return self.advance()

    @staticmethod
    def describe(token):
        if token.kind == "EOF":
            return "end of query"
        if token.kind == "STRING":
            return f'"{token.value}"'
        return f"'{token.value}'"

    # Grammar

    def parse(self):
        node = self.parse_or()
        if self.current.kind != "EOF":
            raise PQLError(f"Unexpected {self.describe(self.current)}", self.current.position)
        return node

    def parse_or(self):
        items = [self.parse_and()]
        while self.is_keyword("OR"):
            self.advance()
            items.append(self.parse_and())
        return items[0] if len(items) == 1 else Or(items)

    def parse_and(self):
        items = [self.parse_not()]
        while self.is_keyword("AND"):
            self.advance()
            items.append(self.parse_not())
        return items[0] if len(items) == 1 else And(items)

    def parse_not(self):
        if self.is_keyword("NOT"):
            self.advance()
            return Not(self.parse_not())
        return self.parse_primary()

    def parse_primary(self):
        token = self.current
        if self.is_punct("("):
            self.advance()
            node = self.parse_or()
            self.expect_punct(")")
            return node

        if token.kind != "IDENT":
            raise PQLError(f"Expected a field or function but found {self.describe(token)}", token.position)

        next_token = self.tokens[self.index + 1]
        if next_token.kind == "PUNCT" and next_token.value == "(":
            return self.parse_func_call()
        return self.parse_comparison()

    def parse_field(self):
        token = self.advance()
        if token.value == "cf" and self.is_punct("["):
            self.advance()
            if self.current.kind != "STRING":
                raise PQLError("Expected a quoted property id inside cf[...]", self.current.position)
            property_id = self.advance().value
            self.expect_punct("]")
            return FieldRef(name="cf", position=token.position, property_id=property_id)
        return FieldRef(name=token.value, position=token.position)

    def parse_comparison(self):
        field = self.parse_field()
        token = self.current

        if token.kind == "OP" and token.value in COMPARISON_OPERATORS:
            self.advance()
            return Comparison(field, token.value, [self.parse_value()], token.position)

        if self.is_keyword("NOT"):
            self.advance()
            self.expect_keyword("IN")
            return Comparison(field, "not_in", self.parse_value_set(), token.position)

        if self.is_keyword("IN"):
            self.advance()
            return Comparison(field, "in", self.parse_value_set(), token.position)

        if self.is_keyword("IS"):
            self.advance()
            negated = False
            if self.is_keyword("NOT"):
                self.advance()
                negated = True
            if self.is_keyword("NULL"):
                self.advance()
                return Comparison(field, "is_not_null" if negated else "is_null", [], token.position)
            if self.is_keyword("EMPTY"):
                self.advance()
                return Comparison(field, "is_not_empty" if negated else "is_empty", [], token.position)
            raise PQLError(
                f"Expected NULL or EMPTY after IS but found {self.describe(self.current)}", self.current.position
            )

        if self.is_keyword("BETWEEN"):
            self.advance()
            if self.is_punct("("):
                self.advance()
                low = self.parse_value()
                self.expect_punct(",")
                high = self.parse_value()
                self.expect_punct(")")
            else:
                low = self.parse_value()
                self.expect_keyword("AND")
                high = self.parse_value()
            return Comparison(field, "between", [low, high], token.position)

        raise PQLError(
            f"Expected an operator after '{field.name}' but found {self.describe(token)}",
            token.position,
        )

    def parse_value_set(self):
        """The right-hand side of IN: a parenthesised list or a set function."""
        if self.current.kind == "IDENT":
            return [self.parse_func_call()]

        self.expect_punct("(")
        values = [self.parse_value()]
        while self.is_punct(","):
            self.advance()
            values.append(self.parse_value())
        self.expect_punct(")")
        if len(values) > MAX_LIST_ITEMS:
            raise PQLError(f"IN lists are limited to {MAX_LIST_ITEMS} values", values[0].position)
        return values

    def parse_value(self):
        token = self.current
        if token.kind in ("STRING", "NUMBER"):
            self.advance()
            return Literal(token.value, token.position)
        if self.is_keyword("TRUE", "FALSE"):
            self.advance()
            return Literal(token.value == "TRUE", token.position)
        if token.kind == "IDENT":
            next_token = self.tokens[self.index + 1]
            if next_token.kind == "PUNCT" and next_token.value == "(":
                return self.parse_func_call()
            raise PQLError(
                f"Unexpected '{token.value}'. Quote string values, e.g. \"{token.value}\"",
                token.position,
            )
        raise PQLError(f"Expected a value but found {self.describe(token)}", token.position)

    def parse_func_call(self):
        name_token = self.advance()
        self.expect_punct("(")
        args = []
        if not self.is_punct(")"):
            args.append(self.parse_value())
            while self.is_punct(","):
                self.advance()
                args.append(self.parse_value())
        self.expect_punct(")")
        return FuncCall(name_token.value, args, name_token.position)


def count_conditions(node):
    """Each comparison and each standalone function call is one condition."""
    if isinstance(node, (And, Or)):
        return sum(count_conditions(item) for item in node.items)
    if isinstance(node, Not):
        return count_conditions(node.item)
    return 1


def parse(text):
    """Parse a PQL string into an AST, enforcing the length and condition limits."""
    if text is None or not text.strip():
        raise PQLError("The PQL query is empty")
    if len(text) > MAX_QUERY_LENGTH:
        raise PQLError(f"The PQL query is longer than {MAX_QUERY_LENGTH} characters")

    node = Parser(text).parse()

    conditions = count_conditions(node)
    if conditions > MAX_CONDITIONS:
        raise PQLError(f"A PQL query may contain at most {MAX_CONDITIONS} conditions, found {conditions}")
    return node
