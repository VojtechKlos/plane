/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Collaboration } from "@tiptap/extension-collaboration";
import React, { useMemo } from "react";
// plane imports
import { cn } from "@plane/utils";
// components
import { EditorContainer } from "@/components/editors/editor-container";
import { EditorContentWrapper } from "@/components/editors/editor-content";
import { BlockMenu, EditorBubbleMenu } from "@/components/menus";
// constants
import { DEFAULT_DISPLAY_CONFIG } from "@/constants/config";
// contexts
import { CollaborationProvider, useCollaboration } from "@/contexts/collaboration-context";
// extensions
import { SideMenuExtension } from "@/extensions";
import { RichTextEditorAdditionalExtensions } from "@/extensions/rich-text-extensions";
// helpers
import { getEditorClassNames } from "@/helpers/common";
// hooks
import { useEditor } from "@/hooks/use-editor";
// types
import type { EditorRefApi, IRichTextEditorProps, TRealtimeConfig, TServerHandler, TUserDetails } from "@/types";

export type ICollaborativeRichTextEditorProps = Omit<IRichTextEditorProps, "initialValue" | "value"> & {
  realtimeConfig: TRealtimeConfig;
  serverHandler?: TServerHandler;
  user: TUserDetails;
};

/** Rich text editor whose content is a Y.js document shared through the live server (work item descriptions). */
function CollaborativeRichTextEditorInner(props: ICollaborativeRichTextEditorProps) {
  const {
    bubbleMenuEnabled = true,
    containerClassName,
    disabledExtensions,
    displayConfig = DEFAULT_DISPLAY_CONFIG,
    dragDropEnabled,
    editable,
    editorClassName = "",
    editorProps,
    extendedEditorProps,
    extensions: externalExtensions = [],
    fileHandler,
    flaggedExtensions,
    forwardedRef,
    getEditorMetaData,
    handleEditorReady,
    id,
    isTouchDevice,
    mentionHandler,
    onAssetChange,
    onChange,
    onEditorFocus,
    onTransaction,
    placeholder,
    showPlaceholderOnEmpty,
    tabIndex,
    workItemIdentifier,
  } = props;

  const { provider, state } = useCollaboration();

  const extensions = useMemo(
    () => [
      Collaboration.configure({ document: provider.document, field: "default" }),
      ...externalExtensions,
      SideMenuExtension({ aiEnabled: false, dragDropEnabled: !!dragDropEnabled }),
      ...RichTextEditorAdditionalExtensions({
        disabledExtensions,
        fileHandler,
        flaggedExtensions,
        extendedEditorProps,
      }),
    ],
    [
      provider,
      externalExtensions,
      dragDropEnabled,
      disabledExtensions,
      fileHandler,
      flaggedExtensions,
      extendedEditorProps,
    ]
  );

  const editor = useEditor({
    disabledExtensions,
    editable,
    editorClassName,
    editorProps,
    // Y.js keeps the history: the regular undo stack would undo other people's changes
    enableHistory: false,
    extendedEditorProps,
    extensions,
    fileHandler,
    flaggedExtensions,
    forwardedRef,
    getEditorMetaData,
    handleEditorReady,
    id,
    isTouchDevice,
    mentionHandler,
    onAssetChange,
    onChange,
    onEditorFocus,
    onTransaction,
    placeholder,
    provider,
    showPlaceholderOnEmpty,
    tabIndex,
  });

  const editorContainerClassName = getEditorClassNames({
    noBorder: true,
    borderOnFocus: false,
    containerClassName,
  });

  if (!editor) return null;

  return (
    <div className={cn("transition-opacity duration-200", !state.isDocReady && "pointer-events-none opacity-0")}>
      <EditorContainer
        displayConfig={displayConfig}
        editor={editor}
        editorContainerClassName={editorContainerClassName}
        id={id}
        isTouchDevice={!!isTouchDevice}
      >
        {bubbleMenuEnabled && (
          <EditorBubbleMenu
            disabledExtensions={disabledExtensions}
            editor={editor}
            extendedEditorProps={extendedEditorProps}
            flaggedExtensions={flaggedExtensions}
          />
        )}
        <BlockMenu
          editor={editor}
          flaggedExtensions={flaggedExtensions}
          disabledExtensions={disabledExtensions}
          workItemIdentifier={workItemIdentifier}
        />
        <div className="flex flex-col">
          <EditorContentWrapper editor={editor} id={id} tabIndex={tabIndex} />
        </div>
      </EditorContainer>
    </div>
  );
}

function CollaborativeRichTextEditor(props: ICollaborativeRichTextEditorProps) {
  const { id, realtimeConfig, serverHandler, user } = props;
  const token = useMemo(() => JSON.stringify(user), [user]);

  return (
    <CollaborationProvider
      docId={id}
      serverUrl={realtimeConfig.url}
      authToken={token}
      onStateChange={serverHandler?.onStateChange}
    >
      <CollaborativeRichTextEditorInner {...props} />
    </CollaborationProvider>
  );
}

const CollaborativeRichTextEditorWithRef = React.forwardRef(function CollaborativeRichTextEditorWithRef(
  props: ICollaborativeRichTextEditorProps,
  ref: React.ForwardedRef<EditorRefApi>
) {
  return (
    <CollaborativeRichTextEditor key={props.id} {...props} forwardedRef={ref as React.MutableRefObject<EditorRefApi>} />
  );
});

CollaborativeRichTextEditorWithRef.displayName = "CollaborativeRichTextEditorWithRef";

export { CollaborativeRichTextEditorWithRef };
