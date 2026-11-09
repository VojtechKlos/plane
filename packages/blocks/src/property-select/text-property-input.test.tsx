/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TextPropertyInput, normalizeTextPropertyValue } from "./text-property-input";

function renderInput(value: string | null = null) {
  const onChange = vi.fn();
  const onDone = vi.fn();
  render(
    <>
      <TextPropertyInput
        value={value}
        onChange={onChange}
        onDone={onDone}
        variant="select-ghost-md"
        placeholder="Add job ID"
      />
      <button type="button">elsewhere</button>
    </>
  );
  return { input: screen.getByRole("textbox", { name: "Add job ID" }), onChange, onDone };
}

describe("normalizeTextPropertyValue", () => {
  it("trims the value", () => {
    expect(normalizeTextPropertyValue("  J-1 ")).toBe("J-1");
  });

  it.each([null, undefined, "", "   "])("turns %j into null", (value) => {
    expect(normalizeTextPropertyValue(value)).toBeNull();
  });
});

describe("TextPropertyInput", () => {
  it("shows the saved value", () => {
    const { input } = renderInput("J-1");
    expect(input).toHaveProperty("value", "J-1");
  });

  it("saves the trimmed value on blur", async () => {
    const user = userEvent.setup();
    const { input, onChange } = renderInput();

    await user.type(input, "  J-42 ");
    await user.click(screen.getByRole("button", { name: "elsewhere" }));

    expect(onChange).toHaveBeenCalledExactlyOnceWith("J-42");
  });

  it("saves on Enter and hands focus back", async () => {
    const user = userEvent.setup();
    const { input, onChange, onDone } = renderInput("J-1");

    await user.clear(input);
    await user.type(input, "J-2{Enter}");

    expect(onChange).toHaveBeenCalledExactlyOnceWith("J-2");
    expect(onDone).toHaveBeenCalledOnce();
    expect(document.activeElement).not.toBe(input);
  });

  it("saves null when the value is cleared", async () => {
    const user = userEvent.setup();
    const { input, onChange } = renderInput("J-1");

    await user.clear(input);
    await user.type(input, "   {Enter}");

    expect(onChange).toHaveBeenCalledExactlyOnceWith(null);
  });

  it("does not save a value that did not change", async () => {
    const user = userEvent.setup();
    const { input, onChange } = renderInput("J-1");

    await user.type(input, " {Enter}");

    expect(onChange).not.toHaveBeenCalled();
    expect(input).toHaveProperty("value", "J-1");
  });

  it("drops the draft on Escape", async () => {
    const user = userEvent.setup();
    const { input, onChange, onDone } = renderInput("J-1");

    await user.type(input, "99{Escape}");

    expect(onChange).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledOnce();
    expect(input).toHaveProperty("value", "J-1");
  });

  it("cannot be edited when disabled", () => {
    render(<TextPropertyInput value="J-1" onChange={vi.fn()} variant="table-cell" placeholder="Job" disabled />);
    expect(screen.getByRole("textbox", { name: "Job" })).toHaveProperty("disabled", true);
  });
});
