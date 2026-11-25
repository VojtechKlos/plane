/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Plain text the way Word and Google Docs put it on the clipboard: one line per block, list markers instead of
// Markdown, link text without the address, tab-separated table cells and nothing for images.

const BLOCK_TAGS = new Set([
  "BLOCKQUOTE",
  "DIV",
  "FIGURE",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "HR",
  "LI",
  "OL",
  "P",
  "PRE",
  "SECTION",
  "TABLE",
  "TBODY",
  "TD",
  "TFOOT",
  "TH",
  "THEAD",
  "TR",
  "UL",
]);
const TEXT_BLOCK_TAGS = new Set(["P", "H1", "H2", "H3", "H4", "H5", "H6"]);
const SILENT_TAGS = new Set(["IMG", "INPUT", "SCRIPT", "STYLE", "TEMPLATE"]);
const BULLETS = ["•", "◦", "▪"];
const NESTED_INDENT = "    ";

const isElement = (node: Node): node is Element => node.nodeType === Node.ELEMENT_NODE;

const inlineText = (node: Node): string => {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue ?? "";
  if (!isElement(node) || SILENT_TAGS.has(node.tagName)) return "";
  if (node.tagName === "BR") return "\n";
  // a block inside inline content is invalid markup, but its text still counts
  if (BLOCK_TAGS.has(node.tagName)) return blockLines(node, 0).join("\n");
  return Array.from(node.childNodes, inlineText).join("");
};

const getListStart = (list: Element): number => {
  const start = Number.parseInt(list.getAttribute("start") ?? "", 10);
  return Number.isNaN(start) ? 1 : start;
};

const listLines = (list: Element, depth: number): string[] => {
  const isTaskList = list.getAttribute("data-type") === "taskList";
  const isOrdered = list.tagName === "OL";
  let number = getListStart(list);
  const lines: string[] = [];

  for (const child of Array.from(list.children)) {
    if (child.tagName !== "LI") {
      // e.g. a list nested directly in a list by pasted markup
      lines.push(...blockToLines(child, depth + 1).map((line) => NESTED_INDENT + line));
      continue;
    }

    let marker: string;
    if (isTaskList) {
      marker = child.getAttribute("data-checked") === "true" ? "☑" : "☐";
    } else if (isOrdered) {
      marker = `${number}.`;
      number += 1;
    } else {
      marker = BULLETS[depth % BULLETS.length];
    }

    // a task item keeps its checkbox in a <label> and its content in a <div>
    const content = isTaskList
      ? (Array.from(child.children).find((element) => element.tagName === "DIV") ?? child)
      : child;
    const itemLines = blockLines(content, depth + 1);
    (itemLines.length > 0 ? itemLines : [""]).forEach((line, index) => {
      lines.push(index === 0 ? `${marker} ${line}` : NESTED_INDENT + line);
    });
  }

  return lines;
};

const tableLines = (table: Element, depth: number): string[] =>
  Array.from(table.querySelectorAll("tr"))
    .filter((row) => row.closest("table") === table)
    .map((row) =>
      Array.from(row.children)
        .filter((cell) => cell.tagName === "TD" || cell.tagName === "TH")
        .map((cell) =>
          blockLines(cell, depth)
            .map((line) => line.trim())
            .filter(Boolean)
            .join(" ")
            .replaceAll("\t", " ")
        )
        .join("\t")
    );

const blockToLines = (element: Element, depth: number): string[] => {
  if (TEXT_BLOCK_TAGS.has(element.tagName)) {
    // a paragraph holding only an image leaves no empty line behind
    if (!element.textContent?.trim() && element.querySelector("img")) return [];
    return inlineText(element).split("\n");
  }

  switch (element.tagName) {
    case "PRE":
      return (element.textContent ?? "").replace(/\n$/, "").split("\n");
    case "UL":
    case "OL":
      return listLines(element, depth);
    case "TABLE":
      return tableLines(element, depth);
    case "HR":
      return [];
    default:
      return blockLines(element, depth);
  }
};

const blockLines = (container: Element, depth: number): string[] => {
  const lines: string[] = [];
  let pendingInline = "";
  const flushInline = () => {
    if (pendingInline.trim()) lines.push(...pendingInline.split("\n"));
    pendingInline = "";
  };

  for (const child of Array.from(container.childNodes)) {
    if (isElement(child) && BLOCK_TAGS.has(child.tagName)) {
      flushInline();
      lines.push(...blockToLines(child, depth));
    } else {
      pendingInline += inlineText(child);
    }
  }
  flushInline();

  return lines;
};

/**
 * Converts copied editor markup (custom components already replaced) to plain text.
 * @param root element whose children are the copied blocks
 */
export const toPlainText = (root: Element): string => blockLines(root, 0).join("\n").replace(/\s+$/, "");
