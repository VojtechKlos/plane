/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TServerMessage, TWorkItemEvent } from "./types";
import { toClientEvent } from "./types";

/** Minimal socket surface the hub needs (a `ws` WebSocket satisfies it). */
export type TEventsSocket = {
  readyState: number;
  send: (data: string) => void;
};

const OPEN = 1;

export type TEventsClient = {
  socket: TEventsSocket;
  userId: string;
  cookie: string;
  workspaceSlug: string | null;
  projectIds: Set<string>;
};

/**
 * Keeps the browsers connected to this live instance and routes work item events to them.
 * Every live replica subscribes to the Redis channel and delivers to its own clients only.
 */
export class WorkItemEventsHub {
  private clients = new Set<TEventsClient>();

  add(client: TEventsClient) {
    this.clients.add(client);
  }

  remove(client: TEventsClient) {
    this.clients.delete(client);
  }

  get size() {
    return this.clients.size;
  }

  /** Deliver an event to the clients of its workspace that are members of its project. Returns the count. */
  dispatch(event: TWorkItemEvent): number {
    const message = JSON.stringify({ type: "event", event: toClientEvent(event) } satisfies TServerMessage);
    let delivered = 0;
    this.clients.forEach((client) => {
      if (client.workspaceSlug !== event.workspace_slug) return;
      if (!client.projectIds.has(event.project_id)) return;
      if (client.socket.readyState !== OPEN) return;
      try {
        client.socket.send(message);
        delivered++;
      } catch {
        // a broken socket is cleaned up by its close handler
      }
    });
    return delivered;
  }

  clear() {
    this.clients.clear();
  }
}

export const workItemEventsHub = new WorkItemEventsHub();
