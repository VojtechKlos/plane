/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { debounce } from "lodash-es";
import { observer } from "mobx-react";
// plane imports
import { CollaborativeRichTextEditorWithRef } from "@plane/editor";
import type { CollaborationState, EditorRefApi, TExtensions } from "@plane/editor";
import { useTranslation } from "@plane/i18n";
import type { EFileAssetType, TNameDescriptionLoader } from "@plane/types";
import { cn, generateRandomColor, getDescriptionPlaceholderI18n, hslToHex } from "@plane/utils";
// components
import { EditorMentionsRoot } from "@/components/editor/embeds/mentions";
import { DescriptionInput } from "@/components/editor/rich-text/description-input";
// hooks
import { useEditorConfig, useEditorMention } from "@/hooks/editor";
import { useEditorAsset } from "@/hooks/store/use-editor-asset";
import { useMember } from "@/hooks/store/use-member";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useUser } from "@/hooks/store/user";
import { useEditorFlagging } from "@/hooks/use-editor-flagging";
import { useParseEditorContent } from "@/hooks/use-parse-editor-content";
// store
import { StoreContext } from "@/lib/store-context";
import { buildLiveWebSocketURL, isWorkItemRealtimeEnabled } from "@/store/realtime/config";
// services
import { WorkspaceService } from "@/services/workspace.service";

const workspaceService = new WorkspaceService();

// a description that is not synced by then falls back to the read-only view
const CONNECT_TIMEOUT_MS = 10_000;

type Props = {
  containerClassName?: string;
  disabled?: boolean;
  disabledExtensions?: TExtensions[];
  editorRef?: React.RefObject<EditorRefApi | null>;
  entityId: string;
  fileAssetType: EFileAssetType;
  initialValue: string | undefined;
  onSubmit: (
    value: { description_html: string; description_json: object | undefined },
    isMigrationUpdate?: boolean
  ) => Promise<void>;
  placeholder?: string | ((isFocused: boolean, value: string) => string);
  projectId?: string;
  setIsSubmitting: (value: TNameDescriptionLoader) => void;
  workspaceSlug: string;
  issueSequenceId?: number;
};

/**
 * Work item description. With realtime on, people who may edit it share one collaborative (Y.js) document
 * through the live server; everyone else sees the saved description, refreshed when it changes.
 * With realtime off it is the regular description input.
 */
export const WorkItemDescriptionInput = observer(function WorkItemDescriptionInput(props: Props) {
  const { disabled, entityId, projectId } = props;
  const rootStore = useContext(StoreContext);
  const [collaborationFailed, setCollaborationFailed] = useState(false);
  const savedDescription = rootStore.issue.issues.getIssueById(entityId)?.description_html;

  useEffect(() => setCollaborationFailed(false), [entityId]);

  if (!isWorkItemRealtimeEnabled()) return <DescriptionInput key={entityId} {...props} />;

  if (disabled || collaborationFailed || !projectId) {
    return (
      <div>
        <DescriptionInput key={entityId} {...props} disabled swrDescription={savedDescription ?? null} />
        {collaborationFailed && !disabled && (
          <p className="px-3 text-caption-sm-regular text-tertiary">
            Live editing is unavailable right now, the description is read-only.{" "}
            <button type="button" className="underline" onClick={() => setCollaborationFailed(false)}>
              Try again
            </button>
          </p>
        )}
      </div>
    );
  }

  return (
    <CollaborativeDescriptionEditor
      key={entityId}
      {...props}
      projectId={projectId}
      onCollaborationFailed={() => setCollaborationFailed(true)}
    />
  );
});

type TCollaborativeProps = Props & {
  projectId: string;
  onCollaborationFailed: () => void;
};

const CollaborativeDescriptionEditor = observer(function CollaborativeDescriptionEditor(props: TCollaborativeProps) {
  const {
    containerClassName,
    disabledExtensions: additionalDisabledExtensions = [],
    editorRef,
    entityId,
    fileAssetType,
    onCollaborationFailed,
    placeholder,
    projectId,
    workspaceSlug,
  } = props;
  const { t } = useTranslation();
  const rootStore = useContext(StoreContext);
  // store hooks
  const { data: currentUser } = useUser();
  const { getWorkspaceBySlug } = useWorkspace();
  const { getUserDetails } = useMember();
  const { uploadEditorAsset, duplicateEditorAsset } = useEditorAsset();
  const { richText: richTextEditorExtensions } = useEditorFlagging({ workspaceSlug, projectId });
  const { fetchMentions } = useEditorMention({
    searchEntity: async (payload) =>
      await workspaceService.searchEntity(workspaceSlug, { ...payload, project_id: projectId }),
  });
  const { getEditorFileHandlers } = useEditorConfig();
  const { getEditorMetaData } = useParseEditorContent({ projectId, workspaceSlug });
  // derived values
  const workspaceId = getWorkspaceBySlug(workspaceSlug)?.id ?? "";
  const syncedRef = useRef(false);

  const realtimeConfig = useMemo(
    () => ({
      url: buildLiveWebSocketURL("collaboration", { documentType: "work_item", projectId, workspaceSlug }),
    }),
    [projectId, workspaceSlug]
  );

  const user = useMemo(
    () => ({
      id: currentUser?.id ?? "",
      name: currentUser?.display_name ?? "",
      color: hslToHex(generateRandomColor(currentUser?.id ?? "")),
    }),
    [currentUser?.id, currentUser?.display_name]
  );

  // a document that never syncs (live server down, no permission) falls back to the read-only view
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!syncedRef.current) onCollaborationFailed();
    }, CONNECT_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [onCollaborationFailed]);

  const serverHandler = useMemo(
    () => ({
      onStateChange: (state: CollaborationState) => {
        if (state.isServerSynced) syncedRef.current = true;
        if (state.stage.kind === "disconnected") onCollaborationFailed();
      },
    }),
    [onCollaborationFailed]
  );

  // the live server saves the description (local and remote edits alike); keep the local copy current
  // for the rest of the UI
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const syncLocalCopy = useCallback(
    debounce((html: string) => rootStore.issue.issues.updateIssue(entityId, { description_html: html }), 1500),
    [entityId]
  );
  useEffect(() => () => syncLocalCopy.flush(), [syncLocalCopy]);

  if (!currentUser || !workspaceId) return null;

  return (
    <CollaborativeRichTextEditorWithRef
      ref={editorRef}
      id={entityId}
      editable
      realtimeConfig={realtimeConfig}
      serverHandler={serverHandler}
      user={user}
      dragDropEnabled
      disabledExtensions={[...richTextEditorExtensions.disabled, ...additionalDisabledExtensions]}
      flaggedExtensions={richTextEditorExtensions.flagged}
      fileHandler={getEditorFileHandlers({
        projectId,
        uploadFile: async (blockId, file) => {
          const { asset_id } = await uploadEditorAsset({
            blockId,
            data: { entity_identifier: entityId, entity_type: fileAssetType },
            file,
            projectId,
            workspaceSlug,
          });
          return asset_id;
        },
        duplicateFile: async (assetId: string) => {
          const { asset_id } = await duplicateEditorAsset({
            assetId,
            entityType: fileAssetType,
            projectId,
            workspaceSlug,
          });
          return asset_id;
        },
        workspaceId,
        workspaceSlug,
      })}
      getEditorMetaData={getEditorMetaData}
      mentionHandler={{
        searchCallback: async (query) => {
          const res = await fetchMentions(query);
          if (!res) throw new Error("Failed in fetching mentions");
          return res;
        },
        renderComponent: EditorMentionsRoot,
        getMentionedEntityDetails: (id) => ({ display_name: getUserDetails(id)?.display_name ?? "" }),
      }}
      extendedEditorProps={{}}
      onChange={(_json, html) => syncLocalCopy(html)}
      placeholder={placeholder ?? ((isFocused, value) => t(getDescriptionPlaceholderI18n(isFocused, value)))}
      containerClassName={cn("relative pb-3 pl-3", containerClassName)}
    />
  );
});
