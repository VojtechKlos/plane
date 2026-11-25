/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useContext, useEffect } from "react";
import { observer } from "mobx-react";
// store
import { StoreContext } from "@/lib/store-context";
import { buildLiveWebSocketURL, isWorkItemRealtimeEnabled } from "@/store/realtime/config";
import { WorkItemEventsClient } from "@/store/realtime/events-client";
import { WorkItemRealtimeSync } from "@/store/realtime/work-item-realtime-sync";

type Props = {
  workspaceSlug: string | undefined;
};

/** Keeps the open views and work item detail in sync with changes other people make (renders nothing). */
export const WorkItemRealtimeRoot = observer(function WorkItemRealtimeRoot({ workspaceSlug }: Props) {
  const rootStore = useContext(StoreContext);
  const userId = rootStore.user.data?.id;

  useEffect(() => {
    if (!workspaceSlug || !userId || !isWorkItemRealtimeEnabled()) return;
    const sync = new WorkItemRealtimeSync(rootStore, workspaceSlug);
    const client = new WorkItemEventsClient({
      url: buildLiveWebSocketURL("events"),
      workspaceSlug,
      onEvent: sync.handleEvent,
      onResync: sync.handleResync,
    });
    client.start();
    // pick up projects joined meanwhile when the user comes back to the tab
    const refreshOnVisible = () => document.visibilityState === "visible" && client.refreshSubscription();
    document.addEventListener("visibilitychange", refreshOnVisible);
    return () => {
      document.removeEventListener("visibilitychange", refreshOnVisible);
      client.stop();
      sync.dispose();
    };
  }, [rootStore, workspaceSlug, userId]);

  return null;
});
