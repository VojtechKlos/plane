/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef } from "react";
import { observer } from "mobx-react";
import { ImageLightbox } from "@plane/blocks/image-lightbox";
import type { TImageLightboxImage } from "@plane/blocks/image-lightbox";
import { useTranslation } from "@plane/i18n";
import type { TIssueServiceType } from "@plane/types";
import { EIssueServiceType } from "@plane/types";
import { convertBytesToSize, renderFormattedDate } from "@plane/utils";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useMember } from "@/hooks/store/use-member";
// local imports
import { downloadAttachment, getAttachmentInlineURL, isPreviewableImageAttachment } from "./helper";

type TIssueAttachmentLightbox = {
  issueId: string;
  disabled?: boolean;
  issueServiceType?: TIssueServiceType;
};

export const IssueAttachmentLightbox = observer(function IssueAttachmentLightbox(props: TIssueAttachmentLightbox) {
  const { issueId, disabled, issueServiceType = EIssueServiceType.ISSUES } = props;
  const { t } = useTranslation();
  // store hooks
  const { getUserDetails } = useMember();
  const {
    attachment: { getAttachmentsByIssueId, getAttachmentById },
    attachmentLightboxId,
    toggleAttachmentLightbox,
    toggleDeleteAttachmentModal,
  } = useIssueDetail(issueServiceType);
  // derived values
  const images: TImageLightboxImage[] = (getAttachmentsByIssueId(issueId) ?? []).flatMap((attachmentId) => {
    const attachment = getAttachmentById(attachmentId);
    const src = attachment && isPreviewableImageAttachment(attachment) ? getAttachmentInlineURL(attachment) : undefined;
    if (!attachment || !src) return [];
    const uploader = attachment.created_by ? getUserDetails(attachment.created_by)?.display_name : undefined;
    const size = convertBytesToSize(attachment.attributes.size);
    return [
      {
        id: attachment.id,
        src,
        name: attachment.attributes.name,
        meta: uploader
          ? `${size} · ${t("attachment.uploaded_by", { name: uploader, date: renderFormattedDate(attachment.updated_at) })}`
          : size,
      },
    ];
  });
  const activeIndex = images.findIndex((image) => image.id === attachmentLightboxId);
  // refs
  // The image this list last showed and where it sat, so a deletion can be told apart from
  // another work item's attachment being opened.
  const shownRef = useRef<{ id: string; index: number } | null>(null);

  useEffect(() => {
    if (activeIndex >= 0 && attachmentLightboxId) {
      shownRef.current = { id: attachmentLightboxId, index: activeIndex };
      return;
    }
    const shown = shownRef.current;
    if (!shown || shown.id !== attachmentLightboxId) return;
    shownRef.current = null;
    // The image on display is gone (deleted): show the one that took its place, or close.
    const fallback = images[Math.min(shown.index, images.length - 1)];
    toggleAttachmentLightbox(fallback?.id ?? null);
  }, [activeIndex, attachmentLightboxId, images, toggleAttachmentLightbox]);

  // Leaving the work item while its image is open must not leave the store reporting an open modal.
  useEffect(
    () => () => {
      if (shownRef.current) toggleAttachmentLightbox(null);
    },
    [toggleAttachmentLightbox]
  );

  const handleDownload = (attachmentId: string) => {
    const attachment = getAttachmentById(attachmentId);
    if (attachment) downloadAttachment(attachment);
  };

  const handleOpenInNewTab = (attachmentId: string) => {
    const src = images.find((image) => image.id === attachmentId)?.src;
    if (src) window.open(src, "_blank", "noopener,noreferrer");
  };

  return (
    <ImageLightbox
      images={images}
      activeId={attachmentLightboxId}
      onActiveChange={toggleAttachmentLightbox}
      onClose={() => toggleAttachmentLightbox(null)}
      onDownload={handleDownload}
      onOpenInNewTab={handleOpenInNewTab}
      onDelete={disabled ? undefined : toggleDeleteAttachmentModal}
    />
  );
});
