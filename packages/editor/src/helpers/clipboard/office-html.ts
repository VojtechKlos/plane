/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Word and Outlook (desktop) put lists on the clipboard as paragraphs styled `mso-list:l0 level1 lfo1` that carry
// their bullet or number as text in a `mso-list:Ignore` span, and images as `file:///` paths into a local temp folder.
// Without this, a pasted list turns into paragraphs starting with "·" and every image into a broken one.

type TOpenList = {
  level: number;
  ordered: boolean;
  list: HTMLElement;
};

const OFFICE_MARKUP_REGEX = /mso-list|src\s*=\s*["']?\s*file:/i;
const MSO_LIST_REGEX = /mso-list\s*:\s*([^;]+)/i;
const LIST_LEVEL_REGEX = /level(\d+)/i;
const NOT_A_LIST_ITEM_REGEX = /^(?:ignore|none|skip)\b/i;
const MARKER_STYLE_REGEX = /mso-list\s*:\s*ignore/i;
// "1.", "1)", "(1)", "1.2.", "a)", "iv." — bullets are symbols or a lone letter such as Word's "o"
const ORDERED_MARKER_REGEX = /^\(?(?:\d{1,3}(?:\.\d{1,3})*[.)]?|(?:[a-z]{1,2}|[ivxlcdm]{1,6})[.)])$/i;
const NUMERIC_MARKER_REGEX = /^\(?(\d{1,3})[.)]?$/;
// a marker typed into the text, for list paragraphs without the `mso-list:Ignore` span
const LEADING_MARKER_REGEX = /^[\s ]*(\(?(?:\d{1,3}|[a-z]{1,2}|[ivxlcdm]{1,6})[.)]|[·•▪◦o§Ø*–-])[\s ]+/i;

const isElement = (node: Node): node is Element => node.nodeType === Node.ELEMENT_NODE;

const normalizeWhitespace = (value: string | null): string => (value ?? "").replace(/[\s ]+/g, " ").trim();

const getListLevel = (element: Element): number | null => {
  const value = MSO_LIST_REGEX.exec(element.getAttribute("style") ?? "")?.[1]?.trim();
  if (!value || NOT_A_LIST_ITEM_REGEX.test(value)) return null;
  return Math.max(1, Number(LIST_LEVEL_REGEX.exec(value)?.[1] ?? 1));
};

const isListParagraph = (node: Node): node is Element =>
  isElement(node) && node.tagName === "P" && getListLevel(node) !== null;

const isIgnorable = (node: Node): boolean =>
  node.nodeType === Node.COMMENT_NODE || (node.nodeType === Node.TEXT_NODE && !node.nodeValue?.trim());

const findMarkerSpan = (element: Element): Element | undefined =>
  Array.from(element.querySelectorAll("span")).find((span) =>
    MARKER_STYLE_REGEX.test(span.getAttribute("style") ?? "")
  );

/** Word wraps the marker span in further spans carrying the bullet font; they go too when they hold nothing else. */
const getOutermostMarkerWrapper = (markerSpan: Element, block: Element): Element => {
  let wrapper = markerSpan;
  while (
    wrapper.parentElement &&
    wrapper.parentElement !== block &&
    normalizeWhitespace(wrapper.parentElement.textContent) === normalizeWhitespace(wrapper.textContent)
  ) {
    wrapper = wrapper.parentElement;
  }
  return wrapper;
};

const findFirstText = (node: Node): Text | null => {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE && child.nodeValue?.trim()) return child as Text;
    const nestedText = findFirstText(child);
    if (nestedText) return nestedText;
  }
  return null;
};

/** Removes the list marker from the paragraph and returns it, e.g. "·", "o", "1." or "a)". */
const takeMarker = (paragraph: Element): string => {
  const markerSpan = findMarkerSpan(paragraph);
  if (markerSpan) {
    const marker = normalizeWhitespace(markerSpan.textContent);
    getOutermostMarkerWrapper(markerSpan, paragraph).remove();
    return marker;
  }

  const text = findFirstText(paragraph);
  const match = text ? LEADING_MARKER_REGEX.exec(text.data) : null;
  if (!text || !match) return "";
  text.data = text.data.slice(match[0].length);
  return match[1];
};

const collectListRun = (firstParagraph: Element): Element[] => {
  const run: Element[] = [];
  for (let node: Node | null = firstParagraph; node; node = node.nextSibling) {
    if (isListParagraph(node)) run.push(node);
    else if (!isIgnorable(node)) break;
  }
  return run;
};

/** Replaces consecutive list paragraphs with nested <ul>/<ol> lists. */
const replaceWithLists = (doc: Document, run: Element[]) => {
  const placeholder = doc.createComment("");
  run[0].before(placeholder);
  const fragment = doc.createDocumentFragment();
  const openLists: TOpenList[] = [];

  for (const paragraph of run) {
    const level = getListLevel(paragraph) ?? 1;
    const marker = takeMarker(paragraph);
    const ordered = ORDERED_MARKER_REGEX.test(marker);

    while (openLists.length > 0 && openLists[openLists.length - 1].level > level) openLists.pop();
    let current = openLists.at(-1);
    // the list type changes on the same level: close that list and start a new one next to it
    if (current && current.level === level && current.ordered !== ordered) {
      openLists.pop();
      current = openLists.at(-1);
    }

    if (!current || current.level < level) {
      const list = doc.createElement(ordered ? "ol" : "ul");
      const start = Number(NUMERIC_MARKER_REGEX.exec(marker)?.[1] ?? 1);
      if (ordered && start !== 1) list.setAttribute("start", String(start));
      if (current) {
        const parentItem = current.list.lastElementChild ?? current.list.appendChild(doc.createElement("li"));
        parentItem.append(list);
      } else {
        fragment.append(list);
      }
      current = { level, ordered, list };
      openLists.push(current);
    }

    const item = doc.createElement("li");
    item.append(paragraph);
    current.list.append(item);
  }

  placeholder.replaceWith(fragment);
};

const convertListParagraphs = (doc: Document): boolean => {
  const converted = new Set<Element>();
  for (const paragraph of Array.from(doc.body.querySelectorAll("p"))) {
    if (converted.has(paragraph) || !isListParagraph(paragraph)) continue;
    const run = collectListRun(paragraph);
    for (const item of run) converted.add(item);
    replaceWithLists(doc, run);
  }
  return converted.size > 0;
};

/** Numbered headings stay headings; their number is kept as text with a single space instead of the tab spacing. */
const tidyNumberedHeadings = (doc: Document): boolean => {
  let changed = false;
  for (const heading of Array.from(doc.body.querySelectorAll("h1, h2, h3, h4, h5, h6"))) {
    const markerSpan = findMarkerSpan(heading);
    if (!markerSpan) continue;
    const marker = normalizeWhitespace(markerSpan.textContent);
    getOutermostMarkerWrapper(markerSpan, heading).replaceWith(marker ? `${marker} ` : "");
    changed = true;
  }
  return changed;
};

const removeLocalImages = (doc: Document): boolean => {
  const localImages = Array.from(doc.querySelectorAll("img")).filter((image) =>
    /^\s*file:/i.test(image.getAttribute("src") ?? "")
  );
  for (const image of localImages) image.remove();
  return localImages.length > 0;
};

/**
 * Rewrites HTML pasted from Word or Outlook so the editor understands it: list paragraphs become real lists and
 * images that point to local files are dropped. Any other HTML is returned unchanged.
 */
export const normalizeOfficeHTML = (html: string): string => {
  if (!OFFICE_MARKUP_REGEX.test(html)) return html;

  const doc = new DOMParser().parseFromString(html, "text/html");
  const removedImages = removeLocalImages(doc);
  const convertedLists = convertListParagraphs(doc);
  const tidiedHeadings = tidyNumberedHeadings(doc);

  return removedImages || convertedLists || tidiedHeadings ? doc.body.innerHTML : html;
};
