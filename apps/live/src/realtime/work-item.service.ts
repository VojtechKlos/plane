/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { TDocumentPayload } from "@plane/types";
import { APIService } from "@/services/api.service";

export type TWorkItemDescription = {
  /** Stored Y.js state, empty when the description was never edited collaboratively. */
  binary: Uint8Array;
  html: string;
  canEdit: boolean;
};

/** Talks to Django on behalf of the connected user (their cookie). */
export class WorkItemRealtimeService extends APIService {
  constructor(private readonly cookie: string) {
    super();
  }

  private headers(extra: Record<string, string> = {}) {
    return { Cookie: this.cookie, ...extra };
  }

  async fetchProjectIds(workspaceSlug: string): Promise<string[]> {
    const response = await this.get(`/api/workspaces/${workspaceSlug}/realtime/projects/`, {
      headers: this.headers(),
    });
    const projectIds = (response?.data as { project_ids?: string[] })?.project_ids;
    return Array.isArray(projectIds) ? projectIds : [];
  }

  private descriptionPath(workspaceSlug: string, projectId: string, issueId: string) {
    return `/api/workspaces/${workspaceSlug}/projects/${projectId}/issues/${issueId}/realtime-description/`;
  }

  async fetchDescription(workspaceSlug: string, projectId: string, issueId: string): Promise<TWorkItemDescription> {
    const response = await this.get(this.descriptionPath(workspaceSlug, projectId, issueId), {
      headers: this.headers(),
    });
    const data = (response?.data ?? {}) as {
      description_binary?: string | null;
      description_html?: string | null;
      can_edit?: boolean;
    };
    return {
      binary: data.description_binary
        ? new Uint8Array(Buffer.from(data.description_binary, "base64"))
        : new Uint8Array(),
      html: data.description_html || "<p></p>",
      canEdit: data.can_edit === true,
    };
  }

  async updateDescription(
    workspaceSlug: string,
    projectId: string,
    issueId: string,
    payload: TDocumentPayload
  ): Promise<void> {
    await this.patch(this.descriptionPath(workspaceSlug, projectId, issueId), payload, {
      headers: this.headers(),
    });
  }
}
