/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { Hocuspocus } from "@hocuspocus/server";
import type Redis from "ioredis";
import { logger } from "@plane/logger";
import { redisManager } from "@/redis";
import type { WorkItemEventsHub } from "./events-hub";
import { DESCRIPTION_REPLACED_KIND, WORK_ITEM_EVENTS_CHANNEL, parseWorkItemEvent } from "./types";
import type { TWorkItemEvent } from "./types";
import { applyExternalDescription } from "./work-item-document";

const DESCRIPTION_LOCK_TTL_SECONDS = 60;

/**
 * Subscribes to the Django event channel and forwards events to the hub.
 * Also merges descriptions changed outside the editor into open collaborative documents.
 */
export class WorkItemEventsSubscriber {
  private sub: Redis | null = null;
  private started = false;

  constructor(
    private readonly hub: WorkItemEventsHub,
    private readonly hocuspocus: Hocuspocus
  ) {}

  async start() {
    if (this.started) return;
    const client = redisManager.getClient();
    if (!client) {
      logger.warn("REALTIME: Redis unavailable, work item events are disabled");
      return;
    }
    this.started = true;
    this.sub = client.duplicate();
    this.sub.on("message", (channel: string, message: string) => {
      if (channel !== WORK_ITEM_EVENTS_CHANNEL) return;
      void this.handleMessage(message);
    });
    this.sub.on("error", (error) => logger.error("REALTIME: Redis subscriber error", error));
    await this.sub.subscribe(WORK_ITEM_EVENTS_CHANNEL);
    logger.info(`REALTIME: Subscribed to ${WORK_ITEM_EVENTS_CHANNEL}`);
  }

  async handleMessage(message: string) {
    const event = parseWorkItemEvent(message);
    if (!event) {
      logger.warn("REALTIME: Ignoring malformed work item event");
      return;
    }
    if (event.kind === DESCRIPTION_REPLACED_KIND && typeof event.description_html === "string") {
      await this.replaceDescription(event);
    }
    this.hub.dispatch(event);
  }

  private async replaceDescription(event: TWorkItemEvent) {
    const html = event.description_html ?? "<p></p>";
    const openIssueIds = event.issue_ids.filter((issueId) => this.hocuspocus.documents.has(issueId));
    await Promise.all(
      openIssueIds.map(async (issueId) => {
        // Every instance holding the document gets the event; only one may apply it, or the content would double.
        const acquired = await this.acquireOnce(`plane:realtime:description-replace:${event.id}:${issueId}`);
        if (acquired) applyExternalDescription(this.hocuspocus, issueId, html);
      })
    );
  }

  private async acquireOnce(key: string): Promise<boolean> {
    const client = redisManager.getClient();
    if (!client) return true;
    try {
      const result = await client.set(key, "1", "EX", DESCRIPTION_LOCK_TTL_SECONDS, "NX");
      return result === "OK";
    } catch (error) {
      logger.error("REALTIME: Failed to take description replace lock", error);
      return false;
    }
  }

  async stop() {
    if (!this.sub) return;
    try {
      await this.sub.quit();
    } catch {
      this.sub.disconnect();
    } finally {
      this.sub = null;
      this.started = false;
    }
  }
}
