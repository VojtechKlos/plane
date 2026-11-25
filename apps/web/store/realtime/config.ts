/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { LIVE_BASE_PATH, LIVE_BASE_URL } from "@plane/constants";

/** Realtime work item updates are on unless VITE_ENABLE_WORK_ITEM_REALTIME is "0". */
export const isWorkItemRealtimeEnabled = (): boolean => process.env.VITE_ENABLE_WORK_ITEM_REALTIME !== "0";

/** WebSocket URL of a live server endpoint, e.g. `events` or `collaboration`. */
export const buildLiveWebSocketURL = (endpoint: string, params: Record<string, string | undefined> = {}): string => {
  const base = LIVE_BASE_URL?.trim() || window.location.origin;
  const url = new URL(base);
  url.protocol = window.location.protocol === "https:" ? "wss" : "ws";
  url.pathname = `${LIVE_BASE_PATH}/${endpoint}`;
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null) url.searchParams.set(key, value);
  });
  return url.toString();
};
