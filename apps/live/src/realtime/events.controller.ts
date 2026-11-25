/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Hocuspocus } from "@hocuspocus/server";
import type { Request } from "express";
import type { RawData, WebSocket } from "ws";
// plane imports
import { Controller, WebSocket as WSDecorator } from "@plane/decorators";
import { logger } from "@plane/logger";
// services
import { UserService } from "@/services/user.service";
// local imports
import { workItemEventsHub } from "./events-hub";
import type { TEventsClient } from "./events-hub";
import { WorkItemEventsSubscriber } from "./events-subscriber";
import type { TClientMessage, TServerMessage } from "./types";
import { WorkItemRealtimeService } from "./work-item.service";

const CLOSE_UNAUTHORIZED = 4401;
const HEARTBEAT_INTERVAL_MS = 30_000;

const send = (ws: WebSocket, message: TServerMessage) => {
  if (ws.readyState === 1) ws.send(JSON.stringify(message));
};

/**
 * `/live/events`: pushes work item change events (ids only) to the browsers of project members.
 * The browser authenticates with its session cookie, then subscribes to a workspace.
 */
@Controller("/events")
export class WorkItemEventsController {
  [key: string]: unknown;
  private readonly subscriber: WorkItemEventsSubscriber;

  constructor(hocusPocusServer: Hocuspocus) {
    this.subscriber = new WorkItemEventsSubscriber(workItemEventsHub, hocusPocusServer);
    this.subscriber.start().catch((error) => logger.error("REALTIME: Failed to start events subscriber", error));
  }

  @WSDecorator("/")
  handleConnection(ws: WebSocket, req: Request) {
    const cookie = req.headers.cookie?.toString() ?? "";
    let client: TEventsClient | null = null;
    // messages that arrive before authentication finishes are handled afterwards, in order
    const pending: string[] = [];
    let alive = true;

    const heartbeat = setInterval(() => {
      if (!alive) {
        ws.terminate();
        return;
      }
      alive = false;
      ws.ping();
    }, HEARTBEAT_INTERVAL_MS);
    ws.on("pong", () => {
      alive = true;
    });

    const handleMessage = async (raw: string) => {
      if (!client) return;
      let message: TClientMessage;
      try {
        message = JSON.parse(raw) as TClientMessage;
      } catch {
        return;
      }
      if (message.type === "ping") {
        send(ws, { type: "pong" });
        return;
      }
      const workspaceSlug = message.type === "subscribe" ? message.workspaceSlug : client.workspaceSlug;
      if (!workspaceSlug) return;
      try {
        const projectIds = await new WorkItemRealtimeService(client.cookie).fetchProjectIds(workspaceSlug);
        client.workspaceSlug = workspaceSlug;
        client.projectIds = new Set(projectIds);
        send(ws, { type: "subscribed", workspaceSlug, projectIds });
      } catch (error) {
        logger.warn("REALTIME: Subscription refused", { workspaceSlug, error: (error as Error)?.message });
        client.workspaceSlug = null;
        client.projectIds = new Set();
        send(ws, { type: "error", message: "subscription_refused" });
      }
    };

    ws.on("message", (data: RawData) => {
      const raw = data.toString();
      if (!client) pending.push(raw);
      else void handleMessage(raw);
    });

    ws.on("close", () => {
      clearInterval(heartbeat);
      if (client) workItemEventsHub.remove(client);
    });

    ws.on("error", (error: Error) => {
      logger.error("REALTIME: Events socket error", error);
    });

    if (!cookie) {
      ws.close(CLOSE_UNAUTHORIZED, "Unauthorized");
      return;
    }

    new UserService()
      .currentUser(cookie)
      .then((user) => {
        if (ws.readyState !== 1) return undefined;
        client = { socket: ws, userId: user.id, cookie, workspaceSlug: null, projectIds: new Set() };
        workItemEventsHub.add(client);
        // replay early messages in order
        return pending.splice(0).reduce((chain, raw) => chain.then(() => handleMessage(raw)), Promise.resolve());
      })
      .catch(() => {
        ws.close(CLOSE_UNAUTHORIZED, "Unauthorized");
      });
  }
}
