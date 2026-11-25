/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
import { normalizeOfficeHTML } from "../office-html";

// list paragraphs as Word for Windows puts them on the clipboard
const bulletItem = (level: number, bullet: string, text: string) =>
  `<p class=MsoListParagraphCxSpMiddle style='margin-left:${level * 36}.0pt;text-indent:-18.0pt;` +
  `mso-list:l0 level${level} lfo1'><![if !supportLists]><span style='font-family:Symbol;mso-fareast-font-family:Symbol'>` +
  `<span style='mso-list:Ignore'>${bullet}<span style='font:7.0pt "Times New Roman"'>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; ` +
  `</span></span></span><![endif]>${text}<o:p></o:p></p>`;

const numberedItem = (number: string, text: string) =>
  `<p class=MsoListParagraphCxSpMiddle style='text-indent:-18.0pt;mso-list:l1 level1 lfo2'><![if !supportLists]>` +
  `<span style='mso-bidi-font-family:Calibri'><span style='mso-list:Ignore'>${number}` +
  `<span style='font:7.0pt "Times New Roman"'>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; </span></span></span><![endif]>${text}` +
  `<o:p></o:p></p>`;

const wordDocument = (body: string) =>
  `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">` +
  `<head><meta charset="utf-8"><style>@list l0:level1 {mso-level-number-format:bullet;}</style></head>` +
  `<body lang=CS style='tab-interval:35.4pt'><!--StartFragment-->${body}<!--EndFragment--></body></html>`;

const parse = (html: string) => {
  const container = document.createElement("div");
  container.innerHTML = html;
  return container;
};

const ownText = (element: Element | null | undefined) =>
  (element?.querySelector(":scope > p")?.textContent ?? "").replace(/\s+/g, " ").trim();

describe("normalizeOfficeHTML", () => {
  it("turns Word bullet paragraphs into nested lists without the bullet characters", () => {
    const output = parse(
      normalizeOfficeHTML(
        wordDocument(
          "<p class=MsoNormal>Úvod<o:p></o:p></p>\r\n" +
            bulletItem(1, "·", "První <b>tučně</b>") +
            "\r\n" +
            bulletItem(2, "o", "Vnořená") +
            "\r\n" +
            bulletItem(1, "·", "Druhá") +
            "\r\n<p class=MsoNormal>Závěr<o:p></o:p></p>"
        )
      )
    );

    const topLevelItems = output.querySelectorAll(":scope > ul > li");
    expect(topLevelItems).toHaveLength(2);
    expect(ownText(topLevelItems[0])).toBe("První tučně");
    expect(topLevelItems[0].querySelector(":scope > p b")?.textContent).toBe("tučně");
    expect(ownText(topLevelItems[0].querySelector(":scope > ul > li"))).toBe("Vnořená");
    expect(ownText(topLevelItems[1])).toBe("Druhá");
    expect(output.textContent).not.toMatch(/[·]/);
    expect(output.innerHTML).not.toMatch(/mso-list:\s*Ignore/i);
    // the surrounding paragraphs stay where they were
    expect(output.querySelector(":scope > p")?.textContent).toBe("Úvod");
    expect(output.querySelector(":scope > ul + p")?.textContent).toBe("Závěr");
  });

  it("turns numbered paragraphs into an ordered list that keeps its first number", () => {
    const output = parse(
      normalizeOfficeHTML(wordDocument(numberedItem("3.", "Třetí krok") + numberedItem("4.", "Čtvrtý krok")))
    );

    const list = output.querySelector("ol");
    expect(list?.getAttribute("start")).toBe("3");
    expect(Array.from(list?.querySelectorAll(":scope > li") ?? [], (item) => ownText(item))).toEqual([
      "Třetí krok",
      "Čtvrtý krok",
    ]);
  });

  it("starts a new list when the list type changes on the same level", () => {
    const output = parse(normalizeOfficeHTML(wordDocument(bulletItem(1, "·", "Odrážka") + numberedItem("1.", "Krok"))));

    expect(Array.from(output.children, (element) => element.tagName)).toEqual(["UL", "OL"]);
    expect(output.querySelector("ol")?.hasAttribute("start")).toBe(false);
  });

  it("removes a list marker typed as text when Word did not mark it", () => {
    const output = parse(normalizeOfficeHTML("<p style='mso-list:l0 level1 lfo1'>1.&nbsp;&nbsp;&nbsp;Bez značky</p>"));

    expect(output.querySelector("ol > li > p")?.textContent).toBe("Bez značky");
  });

  it("keeps numbered headings as headings with their number", () => {
    const output = parse(
      normalizeOfficeHTML(
        "<h1 style='mso-list:l2 level1 lfo3'><![if !supportLists]><span><span style='mso-list:Ignore'>1." +
          "<span style='font:7.0pt \"Times New Roman\"'>&nbsp;&nbsp;&nbsp; </span></span></span><![endif]>Úvod</h1>"
      )
    );

    expect(output.querySelector("h1")?.textContent).toBe("1. Úvod");
    expect(output.querySelector("ol, ul")).toBeNull();
  });

  it("drops images that point to local files", () => {
    const output = parse(
      normalizeOfficeHTML(
        '<p class=MsoNormal>Text<img width=602 height=339 src="file:///C:/Users/x/AppData/Local/Temp/msohtmlclip1/01/clip_image001.png"></p>'
      )
    );

    expect(output.querySelector("img")).toBeNull();
    expect(output.textContent).toBe("Text");
  });

  it("returns other HTML untouched", () => {
    const html = '<ul><li><p>a</p></li></ul><p><img src="https://example.com/a.png"></p>';
    expect(normalizeOfficeHTML(html)).toBe(html);
  });
});
