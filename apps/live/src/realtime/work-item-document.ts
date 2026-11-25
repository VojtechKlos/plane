/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Document, Hocuspocus } from "@hocuspocus/server";
import { getSchema } from "@tiptap/core";
import { generateHTML, generateJSON } from "@tiptap/html/server";
import { updateYFragment } from "@tiptap/y-tiptap";
import * as Y from "yjs";
// plane imports
import {
  CoreEditorExtensionsWithoutProps,
  getAllDocumentFormatsFromRichTextEditorBinaryData,
  getBinaryDataFromRichTextEditorHTMLString,
} from "@plane/editor/server";
import { logger } from "@plane/logger";
import type { TDocumentPayload } from "@plane/types";
// local imports
import { AppError } from "@/lib/errors";
import type { HocusPocusServerContext } from "@/types";
import { WorkItemRealtimeService } from "./work-item.service";

// Work item descriptions use the rich text editor (no title field, unlike pages).
const richTextSchema = getSchema(CoreEditorExtensionsWithoutProps);
const DESCRIPTION_FIELD = "default";

const requireScope = (context: HocusPocusServerContext) => {
  if (!context.workspaceSlug || !context.projectId) {
    throw new AppError("Work item document needs workspaceSlug and projectId", { code: "BAD_REQUEST" });
  }
  return { workspaceSlug: context.workspaceSlug, projectId: context.projectId };
};

/**
 * Called from onAuthenticate for work item documents. Only users who may edit the work item join the
 * collaborative document (everyone else gets a read-only view of the html in the web app), because
 * joining may persist the document (see fetchWorkItemDocument).
 */
export const authorizeWorkItemConnection = async (context: HocusPocusServerContext, issueId: string) => {
  const { workspaceSlug, projectId } = requireScope(context);
  const service = new WorkItemRealtimeService(context.cookie);
  const description = await service.fetchDescription(workspaceSlug, projectId, issueId);
  if (!description.canEdit) {
    throw new AppError("Not allowed to edit the work item description", { code: "AUTH_FORBIDDEN" });
  }
};

export const fetchWorkItemDocument = async (context: HocusPocusServerContext, issueId: string) => {
  const { workspaceSlug, projectId } = requireScope(context);
  const service = new WorkItemRealtimeService(context.cookie);
  const description = await service.fetchDescription(workspaceSlug, projectId, issueId);
  const isNew = description.binary.byteLength === 0;
  // No Y.js state yet (never edited collaboratively): build it from the html.
  const state = isNew
    ? getBinaryDataFromRichTextEditorHTMLString(description.html)
    : reconcileDescriptionBinary(description.binary, description.html);
  if (isNew || state !== description.binary) {
    // Persist right away: Hocuspocus only stores after an edit, and a document rebuilt again on the next load
    // would get a different history that browsers' cached copies could not merge with.
    try {
      await storeWorkItemDocument(context, issueId, state);
    } catch (error) {
      logger.error("REALTIME: Failed to persist the initial work item document", new AppError(error));
    }
  }
  return state;
};

/**
 * The description may have been changed outside the editor (API, MCP, ...) while no one had it open.
 * Merge that html into the stored state with minimal edits instead of rebuilding the document, so the
 * Y.js history stays continuous and browsers' cached copies (IndexedDB) still merge cleanly.
 */
export const reconcileDescriptionBinary = (binary: Uint8Array, html: string): Uint8Array => {
  const { contentHTML } = getAllDocumentFormatsFromRichTextEditorBinaryData(binary);
  if (normalizeHTML(contentHTML) === normalizeHTML(html)) return binary;
  const doc = new Y.Doc();
  Y.applyUpdate(doc, binary);
  replaceDescriptionInYDoc(doc, html);
  // the stored state plus the reconciling edits
  return Y.encodeStateAsUpdate(doc);
};

const normalizeHTML = (html: string) =>
  generateHTML(generateJSON(html || "<p></p>", CoreEditorExtensionsWithoutProps), CoreEditorExtensionsWithoutProps);

export const storeWorkItemDocument = async (context: HocusPocusServerContext, issueId: string, state: Uint8Array) => {
  const { workspaceSlug, projectId } = requireScope(context);
  const service = new WorkItemRealtimeService(context.cookie);
  const { contentBinaryEncoded, contentHTML, contentJSON } = getAllDocumentFormatsFromRichTextEditorBinaryData(state);
  const payload: TDocumentPayload = {
    description_binary: contentBinaryEncoded,
    description_html: contentHTML,
    description_json: contentJSON,
  };
  await service.updateDescription(workspaceSlug, projectId, issueId, payload);
};

/**
 * Turn the document into `html` with minimal Y.js edits, so connected editors keep their state and
 * simply see the content change (replacing the Y.Doc would duplicate content on reconnect).
 */
export const replaceDescriptionInYDoc = (doc: Y.Doc, html: string, origin?: unknown) => {
  const json = generateJSON(html || "<p></p>", CoreEditorExtensionsWithoutProps);
  const node = richTextSchema.nodeFromJSON(json);
  doc.transact(() => {
    updateYFragment(doc, doc.getXmlFragment(DESCRIPTION_FIELD), node, { mapping: new Map(), isOMark: new Map() });
  }, origin);
};

/**
 * Apply a description changed outside the editor to the open document on this instance.
 * The update is attributed to a pseudo connection carrying a real connection's context, so Hocuspocus
 * broadcasts it, syncs it to other instances and stores it with the usual debounce.
 */
export const applyExternalDescription = (instance: Hocuspocus, issueId: string, html: string): boolean => {
  const document: Document | undefined = instance.documents.get(issueId);
  if (!document) return false;
  const connection = document.getConnections()[0];
  if (!connection) return false;
  const context = connection.context as HocusPocusServerContext;
  if (context?.documentType !== "work_item") return false;
  try {
    const origin = { context: { ...context }, socketId: "realtime-description-replace", request: connection.request };
    replaceDescriptionInYDoc(document, html, origin);
    return true;
  } catch (error) {
    logger.error("REALTIME: Failed to apply external description", new AppError(error, { context: { issueId } }));
    return false;
  }
};
