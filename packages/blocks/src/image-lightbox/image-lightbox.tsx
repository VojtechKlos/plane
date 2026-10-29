/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type * as React from "react";
import { Dialog } from "@base-ui/react/dialog";
// plane imports
import {
  AddOutline,
  ChevronLeftOutline,
  ChevronRightOutline,
  CloseOutline,
  DeleteOutline,
  DownloadOutline,
  MinusOutline,
  NewTabOutline,
} from "@makeplane/propel/icons";
import { useTranslation } from "@plane/i18n";
import { cn } from "@plane/utils";

export type TImageLightboxImage = {
  id: string;
  /** Rendered as the `<img>` source. */
  src: string;
  /** File name, shown in the header and used as the image's alt text. */
  name: string;
  /** Secondary header line, e.g. size and uploader. */
  meta?: React.ReactNode;
};

export type ImageLightboxProps = {
  images: TImageLightboxImage[];
  /** The image on display. The lightbox is open while this names one of `images`. */
  activeId: string | null;
  /** Called with the image to show next, from the arrows or the ←/→ keys. */
  onActiveChange: (id: string) => void;
  /** Called whenever the lightbox asks to close — the close button, `Esc`, or a press beside the image. */
  onClose: () => void;
  /** Omit to hide the download action. */
  onDownload?: (id: string) => void;
  /** Omit to hide the open-in-new-tab action. */
  onOpenInNewTab?: (id: string) => void;
  /** Omit to hide the delete action, e.g. when the viewer may not delete. */
  onDelete?: (id: string) => void;
};

const ZOOM_STEPS = [0.5, 1, 1.5, 2, 3];
const MIN_ZOOM = ZOOM_STEPS[0];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];
const WHEEL_ZOOM_SPEED = 0.01;

type TView = { id: string | null; zoom: number; x: number; y: number };

const clampZoom = (zoom: number) => Math.min(Math.max(zoom, MIN_ZOOM), MAX_ZOOM);

const BUTTON_CLASSNAME =
  "grid size-8 shrink-0 place-items-center rounded-md text-white/70 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-2 focus-visible:outline-white disabled:pointer-events-none disabled:text-white/30";

/**
 * A full-screen image viewer: steps through `images`, zooms and pans the one on display, and
 * offers download, open-in-new-tab and delete for it. Controlled through `activeId`.
 */
export function ImageLightbox(props: ImageLightboxProps) {
  const { images, activeId, onActiveChange, onClose, onDownload, onOpenInNewTab, onDelete } = props;
  // plane hooks
  const { t } = useTranslation();
  // derived values
  const activeIndex = activeId ? images.findIndex((image) => image.id === activeId) : -1;
  const activeImage = activeIndex >= 0 ? images[activeIndex] : undefined;
  const previousImage = activeIndex > 0 ? images[activeIndex - 1] : undefined;
  const nextImage = activeIndex >= 0 ? images[activeIndex + 1] : undefined;
  // states
  // The view belongs to one image; moving to another starts it fitted and centered again.
  const [view, setView] = useState<TView>({ id: null, zoom: 1, x: 0, y: 0 });
  const [stage, setStage] = useState<HTMLDivElement | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const currentView = view.id === activeImage?.id ? view : { id: activeImage?.id ?? null, zoom: 1, x: 0, y: 0 };
  const { zoom } = currentView;
  // refs
  const dragStartRef = useRef<{ pointerX: number; pointerY: number; x: number; y: number } | null>(null);

  const setZoom = useCallback(
    (getZoom: (zoom: number) => number) =>
      setView((previous) => {
        const base = previous.id === activeImage?.id ? previous : { id: activeImage?.id ?? null, zoom: 1, x: 0, y: 0 };
        const nextZoom = clampZoom(getZoom(base.zoom));
        // Back at (or below) the fitted size there is nothing to pan, so recenter.
        return nextZoom <= 1 ? { ...base, zoom: nextZoom, x: 0, y: 0 } : { ...base, zoom: nextZoom };
      }),
    [activeImage?.id]
  );

  const zoomIn = useCallback(
    () => setZoom((current) => ZOOM_STEPS.find((step) => step > current) ?? MAX_ZOOM),
    [setZoom]
  );
  const zoomOut = useCallback(
    () => setZoom((current) => ZOOM_STEPS.findLast((step) => step < current) ?? MIN_ZOOM),
    [setZoom]
  );

  // Pinch / ctrl+wheel zoom. React's wheel listener is passive, so it cannot stop the page zooming.
  useEffect(() => {
    if (!stage) return;
    const handleWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      setZoom((current) => current * (1 - event.deltaY * WHEEL_ZOOM_SPEED));
    };
    stage.addEventListener("wheel", handleWheel, { passive: false });
    return () => stage.removeEventListener("wheel", handleWheel);
  }, [stage, setZoom]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    let handled = true;
    if (event.key === "ArrowLeft" && previousImage) onActiveChange(previousImage.id);
    else if (event.key === "ArrowRight" && nextImage) onActiveChange(nextImage.id);
    else if (event.key === "+" || event.key === "=") zoomIn();
    else if (event.key === "-") zoomOut();
    else handled = false;
    if (!handled) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLImageElement>) => {
    if (zoom <= 1 || event.button !== 0) return;
    event.preventDefault();
    // Captured, so the drag follows the pointer off the image and its closing click lands on the image.
    event.currentTarget.setPointerCapture(event.pointerId);
    dragStartRef.current = { pointerX: event.clientX, pointerY: event.clientY, x: currentView.x, y: currentView.y };
    setIsDragging(true);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLImageElement>) => {
    const start = dragStartRef.current;
    if (!start) return;
    setView({
      ...currentView,
      x: start.x + event.clientX - start.pointerX,
      y: start.y + event.clientY - start.pointerY,
    });
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLImageElement>) => {
    if (!dragStartRef.current) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    dragStartRef.current = null;
    setIsDragging(false);
  };

  // A press beside the image dismisses, like a press on a dialog's backdrop.
  const handleStageClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose();
  };

  return (
    <Dialog.Root
      open={!!activeImage}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Popup
          aria-label={activeImage ? `${t("image_lightbox.label")}: ${activeImage.name}` : t("image_lightbox.label")}
          className="fixed inset-0 z-50 flex flex-col bg-black/90 text-white outline-none"
          data-prevent-outside-click
          onKeyDown={handleKeyDown}
        >
          {activeImage && (
            <>
              <div className="flex shrink-0 items-center justify-between gap-4 px-4 py-3">
                <div className="flex min-w-0 flex-col">
                  <Dialog.Title className="truncate text-14 font-medium">{activeImage.name}</Dialog.Title>
                  {activeImage.meta && <div className="truncate text-12 text-white/60">{activeImage.meta}</div>}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {images.length > 1 && (
                    <span className="px-2 text-13 text-white/70 tabular-nums">
                      {activeIndex + 1} / {images.length}
                    </span>
                  )}
                  {onOpenInNewTab && (
                    <button
                      type="button"
                      className={BUTTON_CLASSNAME}
                      aria-label={t("open_in_new_tab")}
                      onClick={() => onOpenInNewTab(activeImage.id)}
                    >
                      <NewTabOutline className="size-4" />
                    </button>
                  )}
                  {onDownload && (
                    <button
                      type="button"
                      className={BUTTON_CLASSNAME}
                      aria-label={t("image_lightbox.download")}
                      onClick={() => onDownload(activeImage.id)}
                    >
                      <DownloadOutline className="size-4" />
                    </button>
                  )}
                  {onDelete && (
                    <button
                      type="button"
                      className={BUTTON_CLASSNAME}
                      aria-label={t("delete")}
                      onClick={() => onDelete(activeImage.id)}
                    >
                      <DeleteOutline className="size-4" />
                    </button>
                  )}
                  <Dialog.Close className={BUTTON_CLASSNAME} aria-label={t("close")}>
                    <CloseOutline className="size-4" />
                  </Dialog.Close>
                </div>
              </div>

              <div
                ref={setStage}
                role="presentation"
                className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden px-16"
                onClick={handleStageClick}
              >
                <img
                  key={activeImage.id}
                  src={activeImage.src}
                  alt={activeImage.name}
                  draggable={false}
                  className={cn("max-h-full max-w-full object-contain transition-transform duration-150 select-none", {
                    "cursor-grab": zoom > 1 && !isDragging,
                    "cursor-grabbing transition-none": isDragging,
                  })}
                  style={{ transform: `translate(${currentView.x}px, ${currentView.y}px) scale(${zoom})` }}
                  onPointerDown={handlePointerDown}
                  onPointerMove={handlePointerMove}
                  onPointerUp={handlePointerUp}
                  onPointerCancel={handlePointerUp}
                />
                {images.length > 1 && (
                  <>
                    <button
                      type="button"
                      className={cn(BUTTON_CLASSNAME, "absolute top-1/2 left-4 size-10 -translate-y-1/2 bg-black/40")}
                      aria-label={t("image_lightbox.previous")}
                      disabled={!previousImage}
                      onClick={() => previousImage && onActiveChange(previousImage.id)}
                    >
                      <ChevronLeftOutline className="size-5" />
                    </button>
                    <button
                      type="button"
                      className={cn(BUTTON_CLASSNAME, "absolute top-1/2 right-4 size-10 -translate-y-1/2 bg-black/40")}
                      aria-label={t("image_lightbox.next")}
                      disabled={!nextImage}
                      onClick={() => nextImage && onActiveChange(nextImage.id)}
                    >
                      <ChevronRightOutline className="size-5" />
                    </button>
                  </>
                )}
              </div>

              <div className="flex shrink-0 items-center justify-center py-3">
                <div className="flex items-center gap-1 rounded-md border border-white/15 bg-black px-1 py-1">
                  <button
                    type="button"
                    className={BUTTON_CLASSNAME}
                    aria-label={t("image_lightbox.zoom_out")}
                    disabled={zoom <= MIN_ZOOM}
                    onClick={zoomOut}
                  >
                    <MinusOutline className="size-4" />
                  </button>
                  <span className="w-12 text-center text-13 tabular-nums" aria-live="polite">
                    {Math.round(zoom * 100)}%
                  </span>
                  <button
                    type="button"
                    className={BUTTON_CLASSNAME}
                    aria-label={t("image_lightbox.zoom_in")}
                    disabled={zoom >= MAX_ZOOM}
                    onClick={zoomIn}
                  >
                    <AddOutline className="size-4" />
                  </button>
                </div>
              </div>
            </>
          )}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

ImageLightbox.displayName = "blocks.ImageLightbox";
