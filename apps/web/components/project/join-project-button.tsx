/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@makeplane/propel/elements/button";
import type { IProject } from "@plane/types";
// components
import { JoinProjectModal } from "@/components/project/join-project-modal";
// hooks
import { useUserPermissions } from "@/hooks/store/user";

type TJoinProjectButtonProps = {
  workspaceSlug: string;
  project: IProject;
};

/**
 * Lets a workspace member who is a Guest of the project, e.g. one added to every
 * project by PROJECT_AUTO_MEMBERSHIP, join it with their workspace role to edit
 * work items. Secret projects still need an invite.
 */
export const JoinProjectButton = observer(function JoinProjectButton(props: TJoinProjectButtonProps) {
  const { workspaceSlug, project } = props;
  // states
  const [isJoinModalOpen, setIsJoinModalOpen] = useState(false);
  // store hooks
  const { allowPermissions } = useUserPermissions();
  // i18n
  const { t } = useTranslation();

  const canJoin =
    project.network !== 0 &&
    allowPermissions([EUserPermissions.GUEST], EUserPermissionsLevel.PROJECT, workspaceSlug, project.id) &&
    allowPermissions([EUserPermissions.MEMBER], EUserPermissionsLevel.WORKSPACE, workspaceSlug);

  if (!canJoin) return null;

  return (
    <>
      <JoinProjectModal
        isOpen={isJoinModalOpen}
        workspaceSlug={workspaceSlug}
        project={project}
        handleClose={() => setIsJoinModalOpen(false)}
      />
      <Button variant="secondary" size="md" stretch="auto" type="button" onClick={() => setIsJoinModalOpen(true)}>
        {t("common.join")}
      </Button>
    </>
  );
});
