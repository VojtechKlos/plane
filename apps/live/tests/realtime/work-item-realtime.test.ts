/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi } from "vitest";
import * as Y from "yjs";
import {
  getAllDocumentFormatsFromRichTextEditorBinaryData,
  getBinaryDataFromRichTextEditorHTMLString,
} from "@plane/editor/server";
import { WorkItemEventsHub } from "@/realtime/events-hub";
import type { TEventsClient } from "@/realtime/events-hub";
import { WorkItemEventsSubscriber } from "@/realtime/events-subscriber";
import { parseWorkItemEvent, toClientEvent } from "@/realtime/types";
import type { TWorkItemEvent } from "@/realtime/types";
import { reconcileDescriptionBinary, replaceDescriptionInYDoc } from "@/realtime/work-item-document";

vi.mock("@/env", () => ({ env: { API_BASE_URL: "http://api.test", LIVE_BASE_PATH: "/live" } }));

const event = (overrides: Partial<TWorkItemEvent> = {}): TWorkItemEvent => ({
  id: "e1",
  type: "work_item.updated",
  kind: "issue.activity.updated",
  workspace_slug: "acme",
  project_id: "p1",
  issue_ids: ["i1"],
  actor_id: "u1",
  ts: "2026-11-25T10:00:00Z",
  ...overrides,
});

const client = (overrides: Partial<TEventsClient> = {}): TEventsClient & { sent: string[] } => {
  const sent: string[] = [];
  return {
    socket: { readyState: 1, send: (data: string) => sent.push(data) },
    userId: "u2",
    cookie: "session=1",
    workspaceSlug: "acme",
    projectIds: new Set(["p1"]),
    sent,
    ...overrides,
  };
};

const docFromHTML = (html: string) => {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, getBinaryDataFromRichTextEditorHTMLString(html));
  return doc;
};

const htmlOf = (doc: Y.Doc) =>
  getAllDocumentFormatsFromRichTextEditorBinaryData(Y.encodeStateAsUpdate(doc)).contentHTML;

describe("work item events hub", () => {
  it("delivers only to members of the event's project in the event's workspace", () => {
    const hub = new WorkItemEventsHub();
    const member = client();
    const otherProject = client({ projectIds: new Set(["p2"]) });
    const otherWorkspace = client({ workspaceSlug: "other" });
    const notSubscribed = client({ workspaceSlug: null });
    [member, otherProject, otherWorkspace, notSubscribed].forEach((c) => hub.add(c));

    expect(hub.dispatch(event())).toBe(1);
    expect(member.sent).toHaveLength(1);
    expect(JSON.parse(member.sent[0])).toEqual({ type: "event", event: toClientEvent(event()) });
    expect(otherProject.sent).toHaveLength(0);
    expect(otherWorkspace.sent).toHaveLength(0);
    expect(notSubscribed.sent).toHaveLength(0);
  });

  it("skips closed sockets and removed clients", () => {
    const hub = new WorkItemEventsHub();
    const closed = client({ socket: { readyState: 3, send: vi.fn() } });
    const removed = client();
    hub.add(closed);
    hub.add(removed);
    hub.remove(removed);

    expect(hub.dispatch(event())).toBe(0);
    expect(removed.sent).toHaveLength(0);
  });

  it("never forwards the internal description html", () => {
    const hub = new WorkItemEventsHub();
    const member = client();
    hub.add(member);

    hub.dispatch(event({ kind: "description_replaced", description_html: "<p>secret</p>" }));

    expect(member.sent[0]).not.toContain("secret");
  });
});

describe("work item event parsing", () => {
  it("accepts a valid event and rejects malformed ones", () => {
    expect(parseWorkItemEvent(JSON.stringify(event()))).toEqual(event());
    expect(parseWorkItemEvent("not json")).toBeNull();
    expect(parseWorkItemEvent(JSON.stringify({ type: "work_item.updated" }))).toBeNull();
  });
});

describe("description replacement", () => {
  it("turns the document into the new html", () => {
    const doc = docFromHTML("<p>first</p><p>second</p>");

    replaceDescriptionInYDoc(doc, "<p>first</p><p>changed</p><p>third</p>");

    const html = htmlOf(doc);
    expect(html).toContain("first");
    expect(html).toContain("changed");
    expect(html).toContain("third");
    expect(html).not.toContain("second");
  });

  it("reaches connected copies as a normal update without duplicating content", () => {
    const server = docFromHTML("<p>shared text</p>");
    const browser = new Y.Doc();
    Y.applyUpdate(browser, Y.encodeStateAsUpdate(server));
    browser.on("update", (update: Uint8Array) => Y.applyUpdate(server, update));
    server.on("update", (update: Uint8Array) => Y.applyUpdate(browser, update));

    replaceDescriptionInYDoc(server, "<p>replaced from the API</p>");

    expect(htmlOf(browser)).toBe(htmlOf(server));
    expect(htmlOf(browser).match(/replaced from the API/g)).toHaveLength(1);
    expect(htmlOf(browser)).not.toContain("shared text");
  });

  it("uses the given transaction origin", () => {
    const doc = docFromHTML("<p>a</p>");
    const origin = { context: { documentType: "work_item" } };
    const origins: unknown[] = [];
    doc.on("update", (_update: Uint8Array, updateOrigin: unknown) => origins.push(updateOrigin));

    replaceDescriptionInYDoc(doc, "<p>b</p>", origin);

    expect(origins).toEqual([origin]);
  });
});

describe("description reconciliation on load", () => {
  it("keeps the stored state when the html already matches", () => {
    const stored = getBinaryDataFromRichTextEditorHTMLString("<p>same</p>");
    expect(reconcileDescriptionBinary(stored, "<p>same</p>")).toBe(stored);
  });

  it("merges an html changed outside the editor so cached client copies stay compatible", () => {
    const stored = getBinaryDataFromRichTextEditorHTMLString("<p>cached by the browser</p>");
    const browserCache = new Y.Doc();
    Y.applyUpdate(browserCache, stored);

    const reconciled = reconcileDescriptionBinary(stored, "<p>edited through the API</p>");
    const server = new Y.Doc();
    Y.applyUpdate(server, reconciled);
    // the browser connects with its cached copy: both sides exchange their state
    Y.applyUpdate(server, Y.encodeStateAsUpdate(browserCache));
    Y.applyUpdate(browserCache, Y.encodeStateAsUpdate(server));

    expect(htmlOf(server)).toBe(htmlOf(browserCache));
    expect(htmlOf(server)).toContain("edited through the API");
    expect(htmlOf(server)).not.toContain("cached by the browser");
  });
});

describe("events subscriber", () => {
  it("forwards parsed events to the hub and drops malformed messages", async () => {
    const hub = new WorkItemEventsHub();
    const dispatch = vi.spyOn(hub, "dispatch");
    const subscriber = new WorkItemEventsSubscriber(hub, { documents: new Map() } as never);

    await subscriber.handleMessage(JSON.stringify(event()));
    await subscriber.handleMessage("{broken");

    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledWith(event());
  });
});
