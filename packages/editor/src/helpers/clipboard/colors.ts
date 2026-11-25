/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Light-theme values of the editor colour variables (src/styles/variables.css, keys from COLORS_LIST).
// Content copied out of the editor lands in documents and e-mails that know neither the variables nor the dark theme.
const TEXT_COLORS: Record<string, string> = {
  gray: "#5c5e63",
  peach: "#ff5b59",
  pink: "#f65385",
  orange: "#fd9038",
  green: "#0fc27b",
  "light-blue": "#17bee9",
  "dark-blue": "#266df0",
  purple: "#9162f9",
};

const BACKGROUND_COLORS: Record<string, string> = {
  gray: "#d6d6d8",
  peach: "#ffd5d7",
  pink: "#fdd4e3",
  orange: "#ffe3cd",
  green: "#c3f0de",
  "light-blue": "#c5eff9",
  "dark-blue": "#c9dafb",
  purple: "#e3d8fd",
};

const EDITOR_COLOR_VARIABLE_REGEX = /^var\(\s*--editor-colors-([\w-]+)-(?:text|background)\s*\)$/;
// anything that could end the declaration or escape the style attribute
const UNSAFE_CSS_VALUE_REGEX = /[;:"'<>{}\\]/;

/**
 * Resolves an editor colour to a value other applications understand.
 * Accepts a COLORS_LIST key, a `var(--editor-colors-…)` reference or a plain CSS colour (a custom value set through the API).
 * @returns the CSS colour, or null when there is none or it cannot be resolved
 */
export const resolveEditorColor = (value: string | null | undefined, type: "text" | "background"): string | null => {
  const trimmedValue = value?.trim();
  if (!trimmedValue || ["none", "null", "undefined"].includes(trimmedValue)) return null;

  const palette = type === "text" ? TEXT_COLORS : BACKGROUND_COLORS;
  const key = EDITOR_COLOR_VARIABLE_REGEX.exec(trimmedValue)?.[1] ?? trimmedValue;
  if (Object.hasOwn(palette, key)) return palette[key];

  if (trimmedValue.startsWith("var(") || UNSAFE_CSS_VALUE_REGEX.test(trimmedValue)) return null;
  return trimmedValue;
};
