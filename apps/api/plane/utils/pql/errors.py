# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.


class PQLError(Exception):
    """An invalid PQL expression. ``position`` is the 0-based character offset
    the problem was detected at, or None when it applies to the whole query."""

    def __init__(self, message, position=None):
        self.message = message
        self.position = position
        super().__init__(str(self))

    def __str__(self):
        if self.position is None:
            return self.message
        return f"{self.message} (at column {self.position + 1})"
