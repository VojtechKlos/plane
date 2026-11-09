/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { cn } from "@plane/utils";
import { getPillButtonClassName } from "../pill-button/helper";

export type TextPropertyInputVariant = "table-cell" | "select-ghost-md" | "pill-md";

export type TextPropertyInputProps = {
  /** The saved value; `null` or an empty string shows the placeholder. */
  value: string | null | undefined;
  /** Called with the trimmed value, or `null` when it is blank, once it differs from `value`. */
  onChange: (value: string | null) => void;
  variant: TextPropertyInputVariant;
  placeholder?: string;
  /** Accessible name; defaults to the placeholder. */
  ariaLabel?: string;
  disabled?: boolean;
  maxLength?: number;
  /** Leading icon, shown by the `pill-md` variant. */
  icon?: ReactNode;
  tabIndex?: number;
  className?: string;
  testId?: string;
  /** Called after Enter or Escape hands focus back, e.g. to refocus a table cell. */
  onDone?: () => void;
};

/** A trimmed text value, or `null` when nothing but whitespace is left. */
export function normalizeTextPropertyValue(value: string | null | undefined): string | null {
  const trimmed = (value ?? "").trim();
  return trimmed === "" ? null : trimmed;
}

const VARIANT_CLASSNAME: Record<TextPropertyInputVariant, string> = {
  "table-cell":
    "h-full w-full rounded-none bg-transparent px-page-x text-caption-md-regular leading-4 text-secondary group-[.selected-issue-row]:bg-accent-primary/5 hover:bg-layer-transparent-hover focus:bg-layer-transparent-active",
  "select-ghost-md":
    "h-7 w-full rounded-sm bg-transparent px-2 text-body-xs-regular text-primary hover:bg-layer-transparent-hover focus:bg-layer-transparent-active",
  "pill-md": "w-36 bg-transparent",
};

/**
 * A single-line text property edited in place. It keeps its own draft while focused and saves on
 * blur or Enter; Escape drops the draft.
 */
export function TextPropertyInput(props: TextPropertyInputProps) {
  const {
    value,
    onChange,
    variant,
    placeholder,
    ariaLabel,
    disabled = false,
    maxLength = 255,
    icon,
    tabIndex,
    className,
    testId,
    onDone,
  } = props;
  const [draft, setDraft] = useState(value ?? "");
  // Escape blurs the input too; this keeps that blur from saving the dropped draft.
  const discardOnBlurRef = useRef(false);

  useEffect(() => {
    setDraft(value ?? "");
  }, [value]);

  const commit = () => {
    const next = normalizeTextPropertyValue(draft);
    setDraft(next ?? "");
    if (next !== normalizeTextPropertyValue(value)) onChange(next);
  };

  const handleBlur = () => {
    if (discardOnBlurRef.current) {
      discardOnBlurRef.current = false;
      return;
    }
    commit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.blur();
      onDone?.();
    } else if (event.key === "Escape") {
      // Keep the Escape from also closing the surrounding modal or peek view.
      event.stopPropagation();
      discardOnBlurRef.current = true;
      setDraft(value ?? "");
      event.currentTarget.blur();
      onDone?.();
    }
  };

  const input = (
    <input
      type="text"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      // A table's keyboard navigation "clicks" `.clickable` on Enter; a click alone does not focus an input.
      onClick={(event) => event.currentTarget.focus()}
      placeholder={placeholder}
      aria-label={ariaLabel ?? placeholder}
      disabled={disabled}
      maxLength={maxLength}
      tabIndex={tabIndex}
      data-testid={testId}
      className={cn(
        "min-w-0 truncate outline-none placeholder:text-placeholder disabled:cursor-not-allowed disabled:text-disabled",
        VARIANT_CLASSNAME[variant],
        variant !== "pill-md" && className
      )}
    />
  );

  if (variant !== "pill-md") return input;

  return (
    <label className={getPillButtonClassName("md", false, cn("cursor-text", className))}>
      {icon}
      {input}
    </label>
  );
}
