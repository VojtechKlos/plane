/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { EditorView } from "@tiptap/pm/view";
// constants
import { CORE_EXTENSIONS } from "@/constants/extension";
// local imports
import type { TClipboardImageSources } from "./external-clipboard";

const MAX_IMAGES = 20;
const MAX_IMAGE_EDGE = 1600;
const IMAGE_NODE_TYPES = new Set<string>([CORE_EXTENSIONS.CUSTOM_IMAGE, CORE_EXTENSIONS.IMAGE]);

const imageToDataURL = (image: HTMLImageElement): string | null => {
  if (!image.complete || !image.naturalWidth || !image.naturalHeight) return null;
  try {
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
  } catch {
    // the canvas is "tainted" when the storage serves the image from another origin without CORS
    return null;
  }
};

/**
 * Copies the images of the current selection as data URIs, taken from the images the editor has already loaded,
 * keyed by the node's `src`. Uploaded images need a Plane login, so a link would show up broken in Word, Google Docs
 * or an e-mail. Best effort: an image that is not loaded yet, or cannot be read, is left out.
 */
export const collectImageDataURLs = (view: EditorView): TClipboardImageSources => {
  const sources = new Map<string, string>();
  const { doc, selection } = view.state;

  for (const { $from, $to } of selection.ranges) {
    doc.nodesBetween($from.pos, $to.pos, (node, pos) => {
      if (sources.size >= MAX_IMAGES) return false;
      if (!IMAGE_NODE_TYPES.has(node.type.name)) return true;

      const src: unknown = node.attrs.src;
      if (typeof src !== "string" || !src || sources.has(src)) return false;
      const dom = view.nodeDOM(pos);
      const image =
        dom instanceof HTMLImageElement ? dom : dom instanceof HTMLElement ? dom.querySelector("img") : null;
      const dataURL = image ? imageToDataURL(image) : null;
      if (dataURL) sources.set(src, dataURL);
      return false;
    });
  }

  return sources;
};
