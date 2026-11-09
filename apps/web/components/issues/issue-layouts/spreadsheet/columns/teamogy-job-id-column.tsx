/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useTranslation } from "@plane/i18n";
import { TextPropertyInput } from "@plane/blocks/property-select";
import type { TIssue } from "@plane/types";

type Props = {
  issue: TIssue;
  onClose: () => void;
  onChange: (issue: TIssue, data: Partial<TIssue>, updates: any) => void;
  disabled: boolean;
};

export const SpreadsheetTeamogyJobIdColumn = observer(function SpreadsheetTeamogyJobIdColumn(props: Props) {
  const { issue, onChange, disabled, onClose } = props;
  const { t } = useTranslation();

  return (
    <div className="h-11 border-b-[0.5px] border-subtle">
      <TextPropertyInput
        value={issue.teamogy_job_id}
        onChange={(value) =>
          onChange(issue, { teamogy_job_id: value }, { changed_property: "teamogy_job_id", change_details: value })
        }
        placeholder={t("common.teamogy_job_id")}
        disabled={disabled}
        onDone={onClose}
        // `.clickable` is what the table's keyboard navigation clicks on Enter / Space in a focused cell.
        className="clickable"
        variant="table-cell"
      />
    </div>
  );
});
