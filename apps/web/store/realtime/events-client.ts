/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/** Event pushed by the live server when a work item changed (ids only; data is fetched through the API). */
export type TWorkItemRealtimeEvent = {
  id: string;
  type: "work_item.created" | "work_item.updated" | "work_item.deleted" | "work_item.comment";
  kind: string | null;
  workspace_slug: string;
  project_id: string;
  issue_ids: string[];
  actor_id: string | null;
  ts: string;
};

type TServerMessage =
  | { type: "subscribed"; workspaceSlug: string; projectIds: string[] }
  | { type: "event"; event: TWorkItemRealtimeEvent }
  | { type: "pong" }
  | { type: "error"; message: string };

export type TEventsClientOptions = {
  url: string;
  workspaceSlug: string;
  onEvent: (event: TWorkItemRealtimeEvent) => void;
  /** Called after a reconnect, when events may have been missed. */
  onResync?: () => void;
  createSocket?: (url: string) => WebSocket;
};

const MAX_BACKOFF_MS = 30_000;
const PING_INTERVAL_MS = 25_000;
const CLOSE_UNAUTHORIZED = 4401;

/** WebSocket connection to `/live/events` with reconnect (exponential backoff) and keep-alive pings. */
export class WorkItemEventsClient {
  private socket: WebSocket | null = null;
  private attempt = 0;
  private hasConnected = false;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly options: TEventsClientOptions) {}

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState <= 1) socket.close(1000, "client stopped");
  }

  /** Ask the server to reload which projects this user belongs to (e.g. after joining a project). */
  refreshSubscription() {
    this.send({ type: "refresh" });
  }

  private send(message: object) {
    if (this.socket?.readyState === 1) this.socket.send(JSON.stringify(message));
  }

  private connect() {
    if (this.stopped) return;
    let socket: WebSocket;
    try {
      socket = this.options.createSocket
        ? this.options.createSocket(this.options.url)
        : new WebSocket(this.options.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.addEventListener("open", () => {
      const isReconnect = this.hasConnected;
      this.attempt = 0;
      this.hasConnected = true;
      this.send({ type: "subscribe", workspaceSlug: this.options.workspaceSlug });
      this.pingTimer = setInterval(() => this.send({ type: "ping" }), PING_INTERVAL_MS);
      if (isReconnect) this.options.onResync?.();
    });

    socket.addEventListener("message", (message: MessageEvent) => {
      let data: TServerMessage;
      try {
        data = JSON.parse(String(message.data)) as TServerMessage;
      } catch {
        return;
      }
      if (data.type === "event" && data.event) this.options.onEvent(data.event);
    });

    socket.addEventListener("close", (event: CloseEvent) => {
      if (this.socket === socket) this.socket = null;
      this.clearTimers();
      // a signed-out session will not get better by retrying quickly
      if (event.code === CLOSE_UNAUTHORIZED) this.attempt = Math.max(this.attempt, 5);
      this.scheduleReconnect();
    });

    // an error is followed by "close", which schedules the reconnect
  }

  private scheduleReconnect() {
    if (this.stopped) return;
    const delay = Math.min(1000 * 2 ** this.attempt, MAX_BACKOFF_MS) * (0.75 + Math.random() * 0.5);
    this.attempt++;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private clearTimers() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.reconnectTimer = null;
    this.pingTimer = null;
  }
}
