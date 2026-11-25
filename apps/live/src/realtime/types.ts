/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** Redis channel Django publishes work item events to (see apps/api/plane/realtime/publisher.py). */
export const WORK_ITEM_EVENTS_CHANNEL = "plane:realtime:work-items";

/** Hocuspocus document type of a work item description. */
export const WORK_ITEM_DOCUMENT_TYPE = "work_item";

/** Event kind Django sends when a description changed outside the collaborative editor. */
export const DESCRIPTION_REPLACED_KIND = "description_replaced";

export type TWorkItemEventType = "work_item.created" | "work_item.updated" | "work_item.deleted" | "work_item.comment";

/** Event as published by Django. `description_html` is internal and never forwarded to browsers. */
export type TWorkItemEvent = {
  id: string;
  type: TWorkItemEventType;
  kind: string | null;
  workspace_slug: string;
  project_id: string;
  issue_ids: string[];
  actor_id: string | null;
  ts: string;
  description_html?: string;
};

/** Event as forwarded to browsers. */
export type TClientWorkItemEvent = Omit<TWorkItemEvent, "description_html">;

/** Messages a browser sends over /live/events. */
export type TClientMessage = { type: "subscribe"; workspaceSlug: string } | { type: "refresh" } | { type: "ping" };

/** Messages the server sends over /live/events. */
export type TServerMessage =
  | { type: "subscribed"; workspaceSlug: string; projectIds: string[] }
  | { type: "event"; event: TClientWorkItemEvent }
  | { type: "pong" }
  | { type: "error"; message: string };

export const parseWorkItemEvent = (raw: string): TWorkItemEvent | null => {
  try {
    const data = JSON.parse(raw) as Partial<TWorkItemEvent>;
    if (
      !data ||
      typeof data.type !== "string" ||
      typeof data.workspace_slug !== "string" ||
      typeof data.project_id !== "string" ||
      !Array.isArray(data.issue_ids)
    )
      return null;
    return data as TWorkItemEvent;
  } catch {
    return null;
  }
};

export const toClientEvent = (event: TWorkItemEvent): TClientWorkItemEvent => {
  const { description_html: _descriptionHtml, ...clientEvent } = event;
  return clientEvent;
};
