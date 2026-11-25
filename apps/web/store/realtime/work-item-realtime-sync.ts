/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { cloneDeep } from "lodash-es";
import { runInAction } from "mobx";
// plane imports
import { setToast } from "@plane/blocks/toast";
import type { IssuePaginationOptions, TIssue, TIssuesResponse } from "@plane/types";
// services
import { IssueArchiveService, IssueCommentService, IssueService } from "@/services/issue";
import { UserService } from "@/services/user.service";
import { WorkspaceService } from "@/services/workspace.service";
// store
import { EIssueGroupedAction } from "@/store/issue/helpers/base-issues.store";
import type { RootStore } from "@/store/root.store";
// local imports
import type { TWorkItemRealtimeEvent } from "./events-client";
import { addEventToBatch, emptyBatch, flattenGroupedIssueIds, reconcileAction } from "./reconcile";
import type { TPendingBatch } from "./reconcile";

const FLUSH_DELAY_MS = 300;

// the subset of a view store (BaseIssuesStore) this module relies on
type TParams = Record<string, unknown>;
type TViewStore = {
  paginationOptions: IssuePaginationOptions | undefined;
  groupedIssueIds: unknown;
  issueFilterStore: {
    getFilterParams: (
      options: IssuePaginationOptions,
      scopeId: string,
      cursor: string | undefined,
      groupId: string | undefined,
      subGroupId: string | undefined
    ) => TParams;
  };
  processIssueResponse: (response: TIssuesResponse) => { issueList: TIssue[] };
  updateIssueList: (
    issue?: TIssue,
    issueBeforeUpdate?: TIssue,
    action?: EIssueGroupedAction.ADD | EIssueGroupedAction.DELETE
  ) => void;
  removeIssueFromList: (issueId: string) => void;
};

type TViewTarget = {
  store: TViewStore;
  /** project of a project-scoped view; undefined for workspace-wide views */
  projectId?: string;
  params: () => TParams;
  fetch: (params: TParams) => Promise<TIssuesResponse>;
  /** full refetch keeping the pagination, used after a reconnect */
  refetch: () => Promise<unknown>;
};

const asStore = (store: unknown) => store as TViewStore;

/** Filter params of a view store's current page setup, the same ones it fetches its list with. */
const paramsOf = (store: TViewStore, scopeId: string) => () =>
  store.paginationOptions
    ? store.issueFilterStore.getFilterParams(store.paginationOptions, scopeId, undefined, undefined, undefined)
    : {};

/**
 * Applies work item events from the live server to the MobX stores:
 * the shared issue map (cards, peek, gantt, calendar re-render from it), the list of the view on screen
 * (membership is decided by the server, since filters are evaluated there) and the open work item detail.
 */
export class WorkItemRealtimeSync {
  private batch: TPendingBatch = emptyBatch();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushing: Promise<void> = Promise.resolve();
  private readonly issueService = new IssueService();
  private readonly commentService = new IssueCommentService();
  private readonly archiveService = new IssueArchiveService();
  private readonly userService = new UserService();
  private readonly workspaceService = new WorkspaceService();

  constructor(
    private readonly rootStore: RootStore,
    private readonly workspaceSlug: string
  ) {}

  handleEvent = (event: TWorkItemRealtimeEvent) => {
    const accepted = addEventToBatch(this.batch, event, { workspaceSlug: this.workspaceSlug });
    if (accepted) this.scheduleFlush();
  };

  /** After a reconnect events may have been missed: refetch the view on screen and the open work item. */
  handleResync = () => {
    const target = this.getActiveTarget();
    void target?.refetch().catch(() => undefined);
    const openIssue = this.getOpenIssue();
    if (openIssue) void this.refreshOpenIssue(openIssue.projectId, openIssue.issueId, true);
  };

  dispose() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.batch = emptyBatch();
  }

  private scheduleFlush() {
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      // flushes run one after another so "before" snapshots stay consistent
      this.flushing = this.flushing
        .then(() => this.flush())
        .catch((error) => {
          console.error("Realtime work item sync failed", error);
        });
    }, FLUSH_DELAY_MS);
  }

  private get issueRoot() {
    return this.rootStore.issue;
  }

  async flush() {
    const batch = this.batch;
    this.batch = emptyBatch();

    if (batch.deleted.size > 0) this.applyDeleted(batch.deleted);

    if (batch.changed.size > 0) {
      // snapshot before merging fresh data, so grouped views can move cards between groups
      const before = new Map<string, TIssue | undefined>();
      batch.changed.forEach((ids) =>
        ids.forEach((id) => before.set(id, cloneDeep(this.issueRoot.issues.getIssueById(id))))
      );

      await Promise.all(
        Array.from(batch.changed.entries()).map(async ([projectId, ids]) => {
          const issues = await this.issueService
            .retrieveIssues(this.workspaceSlug, projectId, Array.from(ids))
            .catch(() => [] as TIssue[]);
          if (issues?.length) this.mergeIssues(issues);
        })
      );

      await this.reconcileActiveView(batch.changed, before);

      const openIssue = this.getOpenIssue();
      if (openIssue && batch.changed.get(openIssue.projectId)?.has(openIssue.issueId))
        await this.refreshOpenIssue(openIssue.projectId, openIssue.issueId, false);
    }

    if (batch.comments.size > 0) {
      const openIssue = this.getOpenIssue();
      if (openIssue && batch.comments.get(openIssue.projectId)?.has(openIssue.issueId))
        await this.refreshComments(openIssue.projectId, openIssue.issueId);
    }
  }

  private mergeIssues(issues: TIssue[]) {
    // keep the server's updated_at (issues.updateIssue would stamp the local time)
    this.issueRoot.issues.addIssue(issues);
  }

  private applyDeleted(ids: Set<string>) {
    const openIssue = this.getOpenIssue();
    runInAction(() => {
      this.allViewStores().forEach((store) => {
        const listed = flattenGroupedIssueIds(store.groupedIssueIds);
        ids.forEach((id) => listed.has(id) && store.removeIssueFromList(id));
      });
      ids.forEach((id) => this.issueRoot.issues.removeIssue(id));
    });
    if (openIssue && ids.has(openIssue.issueId)) {
      setToast({
        type: "info",
        title: "Work item deleted",
        message: "Someone else deleted the work item you have open.",
      });
    }
  }

  /** Ask the server which of the changed work items the view on screen shows now, and update its lists. */
  private async reconcileActiveView(changed: Map<string, Set<string>>, before: Map<string, TIssue | undefined>) {
    const target = this.getActiveTarget();
    if (!target || !target.store.paginationOptions) return;
    const ids = new Set<string>();
    changed.forEach((projectIds, projectId) => {
      if (target.projectId && target.projectId !== projectId) return;
      projectIds.forEach((id) => ids.add(id));
    });
    if (ids.size === 0) return;

    let matched: TIssue[] = [];
    try {
      const response = await target.fetch({ ...target.params(), issue_ids: Array.from(ids).join(",") });
      matched = target.store.processIssueResponse(response).issueList ?? [];
    } catch {
      return;
    }
    if (matched.length) this.mergeIssues(matched);

    const matchedIds = new Set(matched.map((issue) => issue.id));
    const listed = flattenGroupedIssueIds(target.store.groupedIssueIds);
    runInAction(() => {
      ids.forEach((id) => {
        const current = this.issueRoot.issues.getIssueById(id);
        const previous = before.get(id);
        switch (reconcileAction(matchedIds.has(id), listed.has(id))) {
          case "add":
            if (current) target.store.updateIssueList(current, undefined, EIssueGroupedAction.ADD);
            break;
          case "move":
            if (current) target.store.updateIssueList(current, previous ?? current);
            break;
          case "remove":
            target.store.updateIssueList(undefined, previous ?? current, EIssueGroupedAction.DELETE);
            break;
          default:
            break;
        }
      });
    });
  }

  /** Refresh the open work item's fields that list endpoints do not return, and its activity. */
  private async refreshOpenIssue(projectId: string, issueId: string, withComments: boolean) {
    try {
      const issue = await this.issueService.retrieve(this.workspaceSlug, projectId, issueId);
      if (issue) {
        this.issueRoot.issues.updateIssue(issueId, {
          name: issue.name,
          description_html: issue.description_html,
        });
      }
    } catch {
      // deleted or no longer visible: the delete event takes care of it
    }
    await this.issueRoot.issueDetail.activity.fetchActivities(this.workspaceSlug, projectId, issueId).catch(() => []);
    if (withComments) await this.refreshComments(projectId, issueId);
  }

  /** Full comment refetch: the incremental fetch would miss edits and deletions. */
  private async refreshComments(projectId: string, issueId: string) {
    const commentStore = this.issueRoot.issueDetail.comment;
    try {
      const comments = await this.commentService.getIssueComments(this.workspaceSlug, projectId, issueId);
      runInAction(() => {
        commentStore.comments[issueId] = comments.map((comment) => comment.id);
        comments.forEach((comment) => {
          this.issueRoot.issueDetail.commentReaction.applyCommentReactions(
            comment.id,
            comment?.comment_reactions || []
          );
          commentStore.commentMap[comment.id] = comment;
        });
      });
    } catch {
      return;
    }
    await this.issueRoot.issueDetail.activity.fetchActivities(this.workspaceSlug, projectId, issueId).catch(() => []);
  }

  /** The work item open in the peek overview or on the full page (its activity is loaded). */
  private getOpenIssue(): { projectId: string; issueId: string } | undefined {
    const detail = this.issueRoot.issueDetail;
    const peek = detail.peekIssue;
    if (peek?.issueId && peek.projectId) return { projectId: peek.projectId, issueId: peek.issueId };
    const browseId = this.getBrowseIssueId();
    if (!browseId) return undefined;
    const issue = this.issueRoot.issues.getIssueById(browseId);
    return issue?.project_id ? { projectId: issue.project_id, issueId: issue.id } : undefined;
  }

  private getBrowseIssueId(): string | undefined {
    const match = window.location.pathname.match(/\/browse\/([^/]+)/);
    if (!match) return undefined;
    return this.issueRoot.issues.getIssueIdByIdentifier(decodeURIComponent(match[1]));
  }

  private allViewStores(): TViewStore[] {
    const root = this.issueRoot;
    return [
      root.projectIssues,
      root.cycleIssues,
      root.moduleIssues,
      root.projectViewIssues,
      root.workspaceIssues,
      root.profileIssues,
      root.archivedIssues,
    ].map((store) => store as unknown as TViewStore);
  }

  /** The view store behind the layout on screen, derived from the route. */
  getActiveTarget(): TViewTarget | undefined {
    const root = this.issueRoot;
    const ws = this.workspaceSlug;
    const pathname = window.location.pathname;

    if (root.globalViewId) {
      const store = asStore(root.workspaceIssues);
      const viewId = root.globalViewId;
      return {
        store,
        params: paramsOf(store, viewId),
        fetch: (params) => this.workspaceService.getViewIssues(ws, params),
        refetch: () => root.workspaceIssues.fetchIssuesWithExistingPagination(ws, viewId, "mutation"),
      };
    }

    if (root.userId && pathname.includes("/profile/")) {
      const store = asStore(root.profileIssues);
      const userId = root.userId;
      const view = root.profileIssues.currentView;
      return {
        store,
        params: () => {
          const params: TParams = { ...paramsOf(store, userId)(), assignees: undefined, created_by: undefined };
          params.subscriber = undefined;
          if (view === "assigned") params.assignees = userId;
          else if (view === "created") params.created_by = userId;
          else if (view === "subscribed") params.subscriber = userId;
          return params;
        },
        fetch: (params) => this.userService.getUserProfileIssues(ws, userId, params),
        refetch: () => root.profileIssues.fetchIssuesWithExistingPagination(ws, userId, "mutation"),
      };
    }

    const projectId = root.projectId;
    if (!projectId) return undefined;

    if (pathname.includes("/archives/")) {
      const store = asStore(root.archivedIssues);
      return {
        store,
        projectId,
        params: paramsOf(store, projectId),
        fetch: (params) => this.archiveService.getArchivedIssues(ws, projectId, params),
        refetch: () => root.archivedIssues.fetchIssuesWithExistingPagination(ws, projectId, "mutation"),
      };
    }

    const projectScoped = (store: TViewStore, scopeId: string, refetch: () => Promise<unknown>): TViewTarget => ({
      store,
      projectId,
      params: paramsOf(store, scopeId),
      fetch: (params) => this.issueService.getIssues(ws, projectId, params as never),
      refetch,
    });

    if (root.cycleId) {
      const cycleId = root.cycleId;
      return projectScoped(asStore(root.cycleIssues), cycleId, () =>
        root.cycleIssues.fetchIssuesWithExistingPagination(ws, projectId, "mutation", cycleId)
      );
    }
    if (root.moduleId) {
      const moduleId = root.moduleId;
      return projectScoped(asStore(root.moduleIssues), moduleId, () =>
        root.moduleIssues.fetchIssuesWithExistingPagination(ws, projectId, "mutation", moduleId)
      );
    }
    if (root.viewId) {
      const viewId = root.viewId;
      return projectScoped(asStore(root.projectViewIssues), viewId, () =>
        root.projectViewIssues.fetchIssuesWithExistingPagination(ws, projectId, viewId, "mutation")
      );
    }
    return projectScoped(asStore(root.projectIssues), projectId, () =>
      root.projectIssues.fetchIssuesWithExistingPagination(ws, projectId, "mutation")
    );
  }
}
