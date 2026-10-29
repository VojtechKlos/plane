/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ImageLightbox } from "./image-lightbox";
import type { ImageLightboxProps } from "./image-lightbox";

// The English copy for the keys the lightbox uses, so the tests do not wait on the async catalog.
const LABELS: Record<string, string> = {
  close: "Close",
  delete: "Delete",
  open_in_new_tab: "Open in new tab",
  "image_lightbox.label": "Image preview",
  "image_lightbox.previous": "Previous image",
  "image_lightbox.next": "Next image",
  "image_lightbox.zoom_in": "Zoom in",
  "image_lightbox.zoom_out": "Zoom out",
  "image_lightbox.download": "Download",
};

vi.mock("@plane/i18n", () => ({
  useTranslation: () => ({ t: (key: string) => LABELS[key] ?? key }),
}));

const IMAGES = [
  { id: "a", src: "https://example.com/a.png", name: "first.png", meta: "12 KB" },
  { id: "b", src: "https://example.com/b.png", name: "second.png" },
  { id: "c", src: "https://example.com/c.png", name: "third.png" },
];

function renderLightbox(overrides: Partial<ImageLightboxProps> = {}) {
  const props: ImageLightboxProps = {
    images: IMAGES,
    activeId: "b",
    onActiveChange: vi.fn(),
    onClose: vi.fn(),
    onDownload: vi.fn(),
    onOpenInNewTab: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
  const view = render(<ImageLightbox {...props} />);
  return { ...view, props };
}

/** A lightbox button, once the dialog has opened. */
const button = (name: string | RegExp) => screen.findByRole("button", { name });

describe("ImageLightbox open state", () => {
  it("renders nothing without an active image", () => {
    renderLightbox({ activeId: null });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders nothing when the active id is not one of the images", () => {
    renderLightbox({ activeId: "missing" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the active image with its name, meta and position", async () => {
    renderLightbox({ activeId: "a" });
    await screen.findByRole("dialog");
    const image = screen.getByRole("img", { name: "first.png" });
    expect(image.getAttribute("src")).toBe("https://example.com/a.png");
    expect(screen.getByText("12 KB")).toBeDefined();
    expect(screen.getByText("1 / 3")).toBeDefined();
  });

  it("hides the navigation and counter for a single image", async () => {
    renderLightbox({ images: [IMAGES[0]], activeId: "a" });
    await screen.findByRole("dialog");
    expect(await button("Zoom in")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Previous image" })).toBeNull();
    expect(screen.queryByText("1 / 1")).toBeNull();
  });
});

describe("ImageLightbox navigation", () => {
  it("moves to the neighbouring images from the arrows", async () => {
    const user = userEvent.setup();
    const { props } = renderLightbox();
    await user.click(await button("Previous image"));
    expect(props.onActiveChange).toHaveBeenLastCalledWith("a");
    await user.click(await button("Next image"));
    expect(props.onActiveChange).toHaveBeenLastCalledWith("c");
  });

  it("moves to the neighbouring images from the arrow keys", async () => {
    const user = userEvent.setup();
    const { props } = renderLightbox();
    await button("Next image");
    await user.keyboard("{ArrowLeft}");
    expect(props.onActiveChange).toHaveBeenLastCalledWith("a");
    await user.keyboard("{ArrowRight}");
    expect(props.onActiveChange).toHaveBeenLastCalledWith("c");
  });

  it("does not wrap around at either end", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderLightbox({ activeId: "a" });
    expect((await button("Previous image")).hasAttribute("disabled")).toBe(true);
    await user.keyboard("{ArrowLeft}");
    expect(props.onActiveChange).not.toHaveBeenCalled();

    rerender(<ImageLightbox {...props} activeId="c" />);
    expect((await button("Next image")).hasAttribute("disabled")).toBe(true);
    await user.keyboard("{ArrowRight}");
    expect(props.onActiveChange).not.toHaveBeenCalled();
  });
});

describe("ImageLightbox zoom", () => {
  it("steps the zoom with the buttons and keys, within its limits", async () => {
    const user = userEvent.setup();
    renderLightbox();
    await user.click(await button("Zoom in"));
    expect(screen.getByText("150%")).toBeDefined();
    await user.keyboard("+");
    expect(screen.getByText("200%")).toBeDefined();
    await user.keyboard("{-}{-}{-}");
    expect(screen.getByText("50%")).toBeDefined();
    expect((await button("Zoom out")).hasAttribute("disabled")).toBe(true);
  });

  it("starts the next image at the fitted size", async () => {
    const user = userEvent.setup();
    const { props, rerender } = renderLightbox();
    await user.click(await button("Zoom in"));
    expect(screen.getByText("150%")).toBeDefined();
    rerender(<ImageLightbox {...props} activeId="c" />);
    expect(screen.getByText("100%")).toBeDefined();
  });
});

describe("ImageLightbox actions", () => {
  it("reports download, open-in-new-tab and delete for the active image", async () => {
    const user = userEvent.setup();
    const { props } = renderLightbox();
    await user.click(await button("Download"));
    expect(props.onDownload).toHaveBeenCalledWith("b");
    await user.click(await button("Open in new tab"));
    expect(props.onOpenInNewTab).toHaveBeenCalledWith("b");
    await user.click(await button("Delete"));
    expect(props.onDelete).toHaveBeenCalledWith("b");
  });

  it("hides the actions whose handlers are omitted", async () => {
    renderLightbox({ onDelete: undefined, onDownload: undefined, onOpenInNewTab: undefined });
    expect(await button("Close")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Download" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Open in new tab" })).toBeNull();
  });

  it("closes from the close button and from Escape", async () => {
    const user = userEvent.setup();
    const { props } = renderLightbox();
    await user.click(await button("Close"));
    expect(props.onClose).toHaveBeenCalledTimes(1);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(props.onClose).toHaveBeenCalledTimes(2));
  });

  it("does not close from a press on the image itself", async () => {
    const user = userEvent.setup();
    const { props } = renderLightbox();
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("img", { name: "second.png" }));
    expect(props.onClose).not.toHaveBeenCalled();
  });
});
