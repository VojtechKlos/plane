/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TWorkItemRealtimeEvent } from "./events-client";

/** All issue ids of a view store's (sub)grouped id lists. */
export const flattenGroupedIssueIds = (grouped: unknown): Set<string> => {
  const ids = new Set<string>();
  const walk = (value: unknown) => {
    if (!value) return;
    if (Array.isArray(value)) {
      value.forEach((id) => typeof id === "string" && ids.add(id));
      return;
    }
    if (typeof value === "object") Object.values(value as Record<string, unknown>).forEach(walk);
  };
  walk(grouped);
  return ids;
};

export type TReconcileAction = "add" | "move" | "remove" | "none";

/**
 * What to do with an issue in a view, given whether the view's filters still match it (server answer)
 * and whether the view currently lists it.
 */
export const reconcileAction = (matchesView: boolean, isListed: boolean): TReconcileAction => {
  if (matchesView) return isListed ? "move" : "add";
  return isListed ? "remove" : "none";
};

export type TPendingBatch = {
  /** issue ids to refetch, per project */
  changed: Map<string, Set<string>>;
  /** issue ids whose comments changed, per project */
  comments: Map<string, Set<string>>;
  deleted: Set<string>;
};

export const emptyBatch = (): TPendingBatch => ({ changed: new Map(), comments: new Map(), deleted: new Set() });

const addTo = (map: Map<string, Set<string>>, projectId: string, ids: string[]) => {
  const set = map.get(projectId) ?? new Set<string>();
  ids.forEach((id) => set.add(id));
  map.set(projectId, set);
};

/**
 * Merge an event into the batch; returns false when the event is ignored (other workspace, no ids).
 * Own changes are not skipped: they may come from another tab, the API or an agent using the same account.
 * The refetch runs after the change is saved, so it returns at least what this tab applied optimistically.
 */
export const addEventToBatch = (
  batch: TPendingBatch,
  event: TWorkItemRealtimeEvent,
  context: { workspaceSlug: string | undefined }
): boolean => {
  if (!event || event.workspace_slug !== context.workspaceSlug) return false;
  const ids = (event.issue_ids ?? []).filter(Boolean);
  if (ids.length === 0) return false;

  if (event.type === "work_item.deleted") {
    ids.forEach((id) => batch.deleted.add(id));
    return true;
  }
  if (event.type === "work_item.comment") {
    addTo(batch.comments, event.project_id, ids);
    return true;
  }
  addTo(batch.changed, event.project_id, ids);
  return true;
};
