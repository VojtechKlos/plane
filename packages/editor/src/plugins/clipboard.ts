/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Plugin, PluginKey } from "@tiptap/pm/state";
// plane imports
import type { TCustomComponentsMetaData } from "@plane/utils";
// helpers
import { serializeForExternalClipboard } from "@/helpers/clipboard/external-clipboard";
import { collectImageDataURLs } from "@/helpers/clipboard/image-sources";
import { normalizeOfficeHTML } from "@/helpers/clipboard/office-html";

type TArgs = {
  getEditorMetaData: (htmlContent: string) => TCustomComponentsMetaData;
};

/**
 * Copy: other applications get formatted HTML and plain text (no Markdown), like when copying from Word or Google
 * Docs; Plane editors get the original markup through `text/plane-editor-html` (see `handlePaste` in props.ts).
 * Paste: lists and local images pasted from Word or Outlook are converted to markup the editor understands.
 */
export const ClipboardPlugin = (args: TArgs): Plugin => {
  const { getEditorMetaData } = args;

  return new Plugin({
    key: new PluginKey("planeClipboard"),
    props: {
      handleDOMEvents: {
        copy: (view, event) => {
          const { clipboardData } = event;
          // nothing selected: leave the clipboard alone, like ProseMirror does
          if (!clipboardData || view.state.selection.empty) return false;

          try {
            const editorHTML = view.serializeForClipboard(view.state.selection.content()).dom.innerHTML;
            const { html, text } = serializeForExternalClipboard({
              html: editorHTML,
              metaData: getEditorMetaData(editorHTML),
              imageSources: collectImageDataURLs(view),
            });

            event.preventDefault();
            clipboardData.clearData();
            clipboardData.setData("text/plain", text);
            clipboardData.setData("text/html", html);
            clipboardData.setData("text/plane-editor-html", editorHTML);
            return true;
          } catch (error) {
            // the event is untouched, so ProseMirror's own copy still fills the clipboard
            console.error("Failed to prepare the copied content:", error);
            return false;
          }
        },
      },
      transformPastedHTML: (html) => {
        try {
          return normalizeOfficeHTML(html);
        } catch (error) {
          console.error("Failed to normalize the pasted HTML:", error);
          return html;
        }
      },
    },
  });
};
