/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { INLINE_PREVIEWABLE_IMAGE_MIME_TYPES } from "@plane/constants";
import type { TIssueAttachment } from "@plane/types";
import { getFileURL } from "@plane/utils";

/** Whether the attachment is an image the API will serve inline, so it can be previewed. */
export const isPreviewableImageAttachment = (attachment: TIssueAttachment | undefined): boolean => {
  const type = attachment?.attributes.type?.split(";")[0].trim().toLowerCase();
  return !!type && INLINE_PREVIEWABLE_IMAGE_MIME_TYPES.includes(type);
};

/** The attachment's URL served inline, for an `<img>` or a browser tab; the plain URL downloads it. */
export const getAttachmentInlineURL = (attachment: TIssueAttachment): string | undefined => {
  const fileURL = getFileURL(attachment.asset_url);
  if (!fileURL) return undefined;
  return `${fileURL}${fileURL.includes("?") ? "&" : "?"}disposition=inline`;
};

/** Downloads the attachment without leaving the page — the API serves its plain URL as an attachment. */
export const downloadAttachment = (attachment: TIssueAttachment) => {
  const fileURL = getFileURL(attachment.asset_url);
  if (!fileURL) return;
  const link = document.createElement("a");
  link.href = fileURL;
  link.rel = "noopener noreferrer";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
};
