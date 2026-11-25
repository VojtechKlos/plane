/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TCustomComponentsMetaData } from "@plane/utils";
// local imports
import { resolveEditorColor } from "./colors";
import { toPlainText } from "./plain-text";

/** `src` of an image node (asset id or URL) → data URI of the image as the editor shows it */
export type TClipboardImageSources = ReadonlyMap<string, string>;

type TArgs = {
  /** editor markup of the copied selection, i.e. `view.serializeForClipboard(slice).dom.innerHTML` */
  html: string;
  metaData: TCustomComponentsMetaData;
  imageSources?: TClipboardImageSources;
};

type TStyles = Record<string, string | null | undefined>;

const TASK_INDENT_PT = 18;
const MONOSPACE_FONT = "Consolas, 'Courier New', monospace";
const LINK_PROTOCOLS = ["http:", "https:", "mailto:", "tel:"];
const IMAGE_PROTOCOLS = ["http:", "https:"];
const TASK_PARAGRAPH_ATTRIBUTE = "data-clipboard-task";
const TEXT_BLOCK_REGEX = /^(?:P|H[1-6])$/;
// `data-pm-slice` of a selection that starts and ends inside the same text block
const OPEN_SLICE_REGEX = /^[1-9]\d* [1-9]/;
const ALLOWED_ATTRIBUTES: Record<string, string[]> = {
  A: ["href"],
  IMG: ["src", "alt", "width", "height"],
  OL: ["start", "type"],
  TD: ["colspan", "rowspan"],
  TH: ["colspan", "rowspan"],
};
const ALLOWED_STYLE_PROPERTIES = new Set([
  "background-color",
  "border",
  "border-collapse",
  "color",
  "font-family",
  "font-weight",
  "margin-bottom",
  "margin-left",
  "margin-top",
  "padding",
  "text-align",
  "vertical-align",
  "white-space",
]);
const DISCARDED_STYLE_VALUE_REGEX = /var\(|url\(|expression\(|^(?:none|null|undefined)$/i;

const isElement = (node: Node): node is Element => node.nodeType === Node.ELEMENT_NODE;

const unwrap = (element: Element) => element.replaceWith(...Array.from(element.childNodes));

const parseStyle = (element: Element): Map<string, string> => {
  const styles = new Map<string, string>();
  for (const declaration of (element.getAttribute("style") ?? "").split(";")) {
    const separatorIndex = declaration.indexOf(":");
    if (separatorIndex === -1) continue;
    const property = declaration.slice(0, separatorIndex).trim().toLowerCase();
    const value = declaration.slice(separatorIndex + 1).trim();
    if (property && value) styles.set(property, value);
  }
  return styles;
};

const writeStyle = (element: Element, styles: Map<string, string>) => {
  const style = Array.from(styles, ([property, value]) => `${property}: ${value}`).join("; ");
  if (style) element.setAttribute("style", style);
  else element.removeAttribute("style");
};

const addStyles = (element: Element, styles: TStyles) => {
  const currentStyles = parseStyle(element);
  for (const [property, value] of Object.entries(styles)) {
    if (value) currentStyles.set(property, value);
  }
  writeStyle(element, currentStyles);
};

const sanitizeStyle = (element: Element) => {
  if (!element.hasAttribute("style")) return;
  const styles = parseStyle(element);
  for (const [property, value] of styles) {
    if (!ALLOWED_STYLE_PROPERTIES.has(property) || DISCARDED_STYLE_VALUE_REGEX.test(value)) styles.delete(property);
  }
  writeStyle(element, styles);
};

const toSafeURL = (value: string | null, protocols: string[]): string | null => {
  const trimmedValue = value?.trim();
  if (!trimmedValue) return null;
  try {
    const url = new URL(trimmedValue, document.baseURI);
    return protocols.includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
};

const parsePixels = (value: string | null): number | null => {
  const match = /^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$/i.exec(value ?? "");
  const pixels = match ? Math.round(Number(match[1])) : 0;
  return pixels > 0 ? pixels : null;
};

const setDimensions = (target: Element, source: Element) => {
  for (const dimension of ["width", "height"]) {
    const pixels = parsePixels(source.getAttribute(dimension));
    if (pixels) target.setAttribute(dimension, String(pixels));
    else target.removeAttribute(dimension);
  }
};

/** Callout emoji are stored as decimal code points joined by "-" (e.g. "128161" for 💡). */
const decodeEmoji = (codePoints: string | null): string | null => {
  if (!codePoints) return null;
  const values = codePoints.split("-").map(Number);
  if (values.some((value) => !Number.isInteger(value) || value < 0 || value > 0x10ffff)) return null;
  return String.fromCodePoint(...values);
};

const getMentionLabel = (mention: Element, metaData: TCustomComponentsMetaData): string | null => {
  // other entity types (work items, pages, …) cannot be resolved from the metadata
  if ((mention.getAttribute("entity_name") ?? "user_mention") !== "user_mention") return null;
  const userId = mention.getAttribute("entity_identifier");
  const user = metaData.user_mentions.find((userMention) => userMention.id === userId);
  return user?.display_name ? `@${user.display_name}` : null;
};

/** Turns Plane-only markup into standard HTML that both the HTML and the plain-text output are built from. */
const replaceCustomComponents = (
  root: HTMLElement,
  metaData: TCustomComponentsMetaData,
  imageSources: TClipboardImageSources | undefined
) => {
  const doc = root.ownerDocument;

  for (const mention of Array.from(root.querySelectorAll("mention-component"))) {
    const label = getMentionLabel(mention, metaData);
    if (label) mention.replaceWith(label);
    else mention.remove();
  }

  // the legacy image node links an image by URL: embed it when the editor could read it, otherwise keep the URL,
  // which is usually public (handled before image components add more <img> elements below)
  for (const image of Array.from(root.querySelectorAll("img"))) {
    const originalSrc = image.getAttribute("src");
    const src = imageSources?.get(originalSrc ?? "") ?? toSafeURL(originalSrc, IMAGE_PROTOCOLS);
    if (!src) {
      image.remove();
      continue;
    }
    image.setAttribute("src", src);
    setDimensions(image, image);
  }

  // uploaded images are referenced by asset id behind a login, so only an embedded copy works outside Plane
  for (const imageComponent of Array.from(root.querySelectorAll("image-component"))) {
    const dataURL = imageSources?.get(imageComponent.getAttribute("src") ?? "");
    if (!dataURL) {
      imageComponent.remove();
      continue;
    }
    const image = doc.createElement("img");
    image.setAttribute("src", dataURL);
    image.setAttribute("alt", "");
    setDimensions(image, imageComponent);
    const paragraph = doc.createElement("p");
    const alignment = imageComponent.getAttribute("alignment");
    if (alignment === "center" || alignment === "right") addStyles(paragraph, { "text-align": alignment });
    paragraph.append(image);
    imageComponent.replaceWith(paragraph);
  }

  for (const callout of Array.from(root.querySelectorAll('div[data-block-type="callout-component"]'))) {
    const quote = doc.createElement("blockquote");
    quote.append(...Array.from(callout.childNodes));
    const emoji =
      callout.getAttribute("data-logo-in-use") === "icon"
        ? null
        : decodeEmoji(callout.getAttribute("data-emoji-unicode"));
    if (emoji) {
      const firstBlock = quote.firstElementChild;
      if (firstBlock?.tagName === "P") {
        firstBlock.prepend(`${emoji} `);
      } else {
        const paragraph = doc.createElement("p");
        paragraph.append(emoji);
        quote.prepend(paragraph);
      }
    }
    callout.replaceWith(quote);
  }

  for (const rule of Array.from(root.querySelectorAll('div[data-type="horizontalRule"]'))) {
    rule.replaceWith(doc.createElement("hr"));
  }
};

const getTaskListDepth = (list: Element, root: Element): number => {
  let depth = 0;
  for (let parent = list.parentElement; parent && parent !== root; parent = parent.parentElement) {
    if (parent.tagName === "UL" && parent.getAttribute("data-type") === "taskList") depth += 1;
  }
  return depth;
};

/**
 * Checklists become "☐ task" / "☑ task" paragraphs indented by nesting level: no other application can paste Plane's
 * checkbox markup, and a list without bullets is rendered differently by every one of them.
 */
const convertTaskLists = (root: HTMLElement) => {
  const doc = root.ownerDocument;
  const taskLists = Array.from(root.querySelectorAll('ul[data-type="taskList"]'));
  const depths = new Map(taskLists.map((list) => [list, getTaskListDepth(list, root)]));

  // innermost lists first, so an item already holds the paragraphs of the tasks nested in it
  for (const list of taskLists.toReversed()) {
    const depth = depths.get(list) ?? 0;
    const fragment = doc.createDocumentFragment();

    for (const item of Array.from(list.children)) {
      if (item.tagName !== "LI") {
        fragment.append(item);
        continue;
      }

      const content = Array.from(item.children).find((child) => child.tagName === "DIV") ?? item;
      const nodes = Array.from(content.childNodes).filter((node) => !(isElement(node) && node.tagName === "LABEL"));
      const firstNode = nodes[0];
      let taskParagraph: Element;
      if (
        firstNode &&
        isElement(firstNode) &&
        firstNode.tagName === "P" &&
        !firstNode.hasAttribute(TASK_PARAGRAPH_ATTRIBUTE)
      ) {
        taskParagraph = firstNode;
        nodes.shift();
      } else {
        taskParagraph = doc.createElement("p");
      }
      taskParagraph.prepend(`${item.getAttribute("data-checked") === "true" ? "☑" : "☐"} `);
      taskParagraph.setAttribute(TASK_PARAGRAPH_ATTRIBUTE, "");
      if (depth > 0) addStyles(taskParagraph, { "margin-left": `${depth * TASK_INDENT_PT}pt` });
      fragment.append(taskParagraph);

      for (const node of nodes) {
        if (isElement(node) && !node.hasAttribute(TASK_PARAGRAPH_ATTRIBUTE)) {
          addStyles(node, { "margin-left": `${(depth + 1) * TASK_INDENT_PT}pt` });
        }
        fragment.append(node);
      }
    }

    list.replaceWith(fragment);
  }
};

const cleanUpElement = (element: Element) => {
  switch (element.tagName) {
    case "A": {
      const href = toSafeURL(element.getAttribute("href"), LINK_PROTOCOLS);
      if (href) element.setAttribute("href", href);
      else unwrap(element);
      break;
    }
    case "TD":
    case "TH":
      for (const name of ["colspan", "rowspan"]) {
        if (!(Number(element.getAttribute(name)) > 1)) element.removeAttribute(name);
      }
      break;
    case "OL": {
      const start = Number(element.getAttribute("start"));
      if (!Number.isInteger(start) || start === 1 || !element.hasAttribute("start")) element.removeAttribute("start");
      if (!["a", "A", "i", "I"].includes(element.getAttribute("type") ?? "")) element.removeAttribute("type");
      break;
    }
    case "SPAN":
    case "LABEL":
      if (!element.hasAttributes()) unwrap(element);
      break;
    case "INPUT":
      element.remove();
      break;
    default:
      // unknown custom elements (e.g. embeds) mean nothing outside Plane
      if (element.tagName.includes("-")) {
        if (element.textContent?.trim()) unwrap(element);
        else element.remove();
      }
  }
};

/** Inline styles and a whitelist of attributes, so the markup looks right in Word, Google Docs and e-mail. */
const applyPortableFormatting = (root: HTMLElement) => {
  convertTaskLists(root);

  for (const span of Array.from(root.querySelectorAll("span[data-text-color], span[data-background-color]"))) {
    addStyles(span, {
      color: resolveEditorColor(span.getAttribute("data-text-color"), "text"),
      "background-color": resolveEditorColor(span.getAttribute("data-background-color"), "background"),
    });
  }

  for (const table of Array.from(root.querySelectorAll("table"))) {
    addStyles(table, { "border-collapse": "collapse" });
  }
  for (const cell of Array.from(root.querySelectorAll("td, th"))) {
    const isHeader = cell.tagName === "TH";
    addStyles(cell, {
      border: "1px solid #d0d0d0",
      padding: "4px 8px",
      "vertical-align": "top",
      "text-align": isHeader ? "left" : null,
      "font-weight": isHeader ? "bold" : null,
      "background-color": resolveEditorColor(cell.getAttribute("background"), "background"),
    });
  }

  for (const pre of Array.from(root.querySelectorAll("pre"))) {
    addStyles(pre, {
      "font-family": MONOSPACE_FONT,
      "white-space": "pre-wrap",
      "background-color": "#f5f5f5",
      padding: "8px",
    });
  }
  for (const code of Array.from(root.querySelectorAll("code"))) {
    if (!code.closest("pre")) addStyles(code, { "font-family": MONOSPACE_FONT });
  }

  for (const paragraph of Array.from(root.querySelectorAll("p"))) {
    // Plane keeps paragraphs close together; inside list items and cells any gap would stretch the list or the row
    const isCompact = paragraph.parentElement?.closest("li, td, th") != null;
    addStyles(paragraph, { "margin-top": "0", "margin-bottom": isCompact ? "0" : "6pt" });
    // an empty paragraph would collapse, but it is an intentional blank line
    if (!paragraph.hasChildNodes()) paragraph.append(root.ownerDocument.createElement("br"));
  }

  for (const element of Array.from(root.querySelectorAll("*"))) {
    const allowedAttributes = ALLOWED_ATTRIBUTES[element.tagName] ?? [];
    for (const { name } of Array.from(element.attributes)) {
      if (name !== "style" && !allowedAttributes.includes(name)) element.removeAttribute(name);
    }
    sanitizeStyle(element);
    cleanUpElement(element);
  }
};

/**
 * Builds what other applications get on copy: HTML that keeps the formatting in Word, Google Docs, e-mail and chat,
 * and plain text without Markdown syntax, the same way Word and Google Docs fill the clipboard.
 */
export const serializeForExternalClipboard = ({
  html,
  metaData,
  imageSources,
}: TArgs): { html: string; text: string } => {
  // an inert document: nothing in the copied markup is loaded or run while it is rewritten
  const root = document.implementation.createHTMLDocument("").createElement("div");
  root.innerHTML = html;

  // words copied from inside one paragraph or heading are pasted inline, without the block and its spacing,
  // as with a partial paragraph copied from Word or Google Docs
  const firstElement = root.firstElementChild;
  const isInlineSelection =
    root.childElementCount === 1 &&
    firstElement !== null &&
    TEXT_BLOCK_REGEX.test(firstElement.tagName) &&
    OPEN_SLICE_REGEX.test(firstElement.getAttribute("data-pm-slice") ?? "");

  replaceCustomComponents(root, metaData, imageSources);
  const text = toPlainText(root);
  applyPortableFormatting(root);

  const block = root.firstElementChild;
  const output = isInlineSelection && root.childElementCount === 1 && block ? block.innerHTML : root.innerHTML;
  return { html: output, text };
};
