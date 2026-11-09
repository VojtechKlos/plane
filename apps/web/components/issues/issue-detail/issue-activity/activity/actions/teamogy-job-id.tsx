/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { HashOutline } from "@makeplane/propel/icons";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// components
import { IssueActivityBlockComponent } from "./";

type TIssueTeamogyJobIdActivity = { activityId: string; ends: "top" | "bottom" | undefined };

export const IssueTeamogyJobIdActivity = observer(function IssueTeamogyJobIdActivity(
  props: TIssueTeamogyJobIdActivity
) {
  const { activityId, ends } = props;
  // hooks
  const {
    activity: { getActivityById },
  } = useIssueDetail();

  const activity = getActivityById(activityId);

  if (!activity) return <></>;
  return (
    <IssueActivityBlockComponent
      icon={<HashOutline width={14} height={14} className="text-secondary" aria-hidden="true" />}
      activityId={activityId}
      ends={ends}
    >
      {activity.new_value ? (
        <>
          set the Teamogy job ID to <span className="font-medium text-primary">{activity.new_value}</span>.
        </>
      ) : (
        <>removed the Teamogy job ID.</>
      )}
    </IssueActivityBlockComponent>
  );
});
