/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Editor, Extension } from "@tiptap/core";
import { TaskItem } from "@tiptap/extension-task-item";
import { TaskList } from "@tiptap/extension-task-list";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { Slice } from "@tiptap/pm/model";
import { StarterKit } from "@tiptap/starter-kit";
import { afterEach, describe, expect, it } from "vitest";
import { ClipboardPlugin } from "../clipboard";
import { DropHandlerPlugin } from "../drop";

let editor: Editor;

const ClipboardTestExtension = Extension.create({
  name: "clipboardTest",
  addProseMirrorPlugins() {
    return [ClipboardPlugin({ getEditorMetaData: () => ({ file_assets: [], user_mentions: [] }) })];
  },
});

const createEditor = (content: string) => {
  editor = new Editor({
    element: document.createElement("div"),
    content,
    extensions: [StarterKit, TaskList, TaskItem.configure({ nested: true }), ClipboardTestExtension],
  });
  return editor;
};

const copy = () => {
  const clipboardData = new DataTransfer();
  const event = new ClipboardEvent("copy", { clipboardData, bubbles: true, cancelable: true });
  editor.view.dom.dispatchEvent(event);
  return { clipboardData, event };
};

const findChild = (type: string): ProseMirrorNode | undefined => {
  let found: ProseMirrorNode | undefined;
  editor.state.doc.forEach((node) => {
    if (!found && node.type.name === type) found = node;
  });
  return found;
};

afterEach(() => editor.destroy());

describe("ClipboardPlugin", () => {
  it("puts formatted HTML, plain text without Markdown and the editor markup on the clipboard", () => {
    createEditor("<h2>Nadpis</h2><p>Ahoj <strong>světe</strong></p><ul><li><p>bod</p></li></ul>");
    editor.commands.selectAll();
    const { clipboardData, event } = copy();

    expect(event.defaultPrevented).toBe(true);
    expect(clipboardData.getData("text/plain")).toBe("Nadpis\nAhoj světe\n• bod");
    expect(clipboardData.getData("text/html")).toContain("<h2>Nadpis</h2>");
    expect(clipboardData.getData("text/html")).toContain("<strong>světe</strong>");
    expect(clipboardData.getData("text/html")).not.toContain("data-pm-slice");
    expect(clipboardData.getData("text/plane-editor-html")).toContain("data-pm-slice");
  });

  it("leaves the clipboard alone when nothing is selected", () => {
    createEditor("<p>Ahoj</p>");
    editor.commands.setTextSelection(2);
    const { clipboardData, event } = copy();

    expect(event.defaultPrevented).toBe(false);
    expect(clipboardData.types).toHaveLength(0);
  });

  it("pastes a Word list as a real list", () => {
    createEditor("<p></p>");
    editor.view.pasteHTML(
      "<p style='mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>·<span>&nbsp;&nbsp; </span></span>Jedna</p>" +
        "<p style='mso-list:l0 level2 lfo1'><span style='mso-list:Ignore'>o<span>&nbsp;&nbsp; </span></span>Vnořená</p>" +
        "<p style='mso-list:l1 level1 lfo2'><span style='mso-list:Ignore'>2.<span>&nbsp;&nbsp; </span></span>Dva</p>"
    );

    const bulletList = findChild("bulletList");
    expect(bulletList?.textContent).toBe("JednaVnořená");
    expect(bulletList?.child(0).child(1).type.name).toBe("bulletList");
    const orderedList = findChild("orderedList");
    expect(orderedList?.attrs.start).toBe(2);
    expect(orderedList?.textContent).toBe("Dva");
    expect(editor.state.doc.textContent).not.toContain("·");
  });
});

describe("DropHandlerPlugin", () => {
  const paste = (types: string[], slice: Slice) => {
    const plugin = DropHandlerPlugin({ editor });
    // a type that is never uploaded, so the test only sees which content wins
    const file = new File(["x"], "obrazek.bin", { type: "application/x-plane-test" });
    const event = { clipboardData: { files: [file], types }, preventDefault: () => {} } as unknown as ClipboardEvent;
    return plugin.props.handlePaste?.call(plugin, editor.view, event, slice);
  };

  it("pastes the formatted content when the clipboard also holds a picture of it", () => {
    createEditor("<p>Tabulka z Excelu</p>");
    const slice = editor.state.doc.slice(0, editor.state.doc.content.size);

    expect(paste(["text/plain", "text/html", "Files"], slice)).toBe(false);
  });

  it("takes the files when there is no HTML or the HTML holds no text", () => {
    createEditor("<p>Text</p>");
    const slice = editor.state.doc.slice(0, editor.state.doc.content.size);

    expect(paste(["text/html", "Files"], Slice.empty)).toBe(true);
    expect(paste(["text/plain", "Files"], slice)).toBe(true);
  });
});
