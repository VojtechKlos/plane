/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
import type { TCustomComponentsMetaData } from "@plane/utils";
import { serializeForExternalClipboard } from "../external-clipboard";
import type { TClipboardImageSources } from "../external-clipboard";

const metaData: TCustomComponentsMetaData = {
  file_assets: [],
  user_mentions: [{ id: "user-1", display_name: "vojtech", url: "https://plane.example.com/ws/profile/user-1" }],
};

const serialize = (html: string, imageSources?: TClipboardImageSources) =>
  serializeForExternalClipboard({ html, metaData, imageSources });

const parse = (html: string) => {
  const container = document.createElement("div");
  container.innerHTML = html;
  return container;
};

describe("serializeForExternalClipboard", () => {
  it("copies words from one paragraph as inline formatted text and as plain text without Markdown", () => {
    const { html, text } = serialize(
      '<p class="editor-paragraph-block" data-pm-slice="1 1 []">Ahoj <strong>tučně</strong>, <em>kurzíva</em>, ' +
        "<u>podtržení</u>, <s>přeškrtnutí</s> a " +
        '<a target="_blank" rel="noopener noreferrer nofollow" class="text-accent-secondary underline" ' +
        'href="https://example.com/zadani">zadání</a>.</p>'
    );

    expect(text).toBe("Ahoj tučně, kurzíva, podtržení, přeškrtnutí a zadání.");
    expect(html).toBe(
      "Ahoj <strong>tučně</strong>, <em>kurzíva</em>, <u>podtržení</u>, <s>přeškrtnutí</s> a " +
        '<a href="https://example.com/zadani">zadání</a>.'
    );
  });

  it("keeps blocks, alignment and blank lines, one line per block in plain text", () => {
    const { html, text } = serialize(
      '<h2 class="editor-heading-block" data-id="a" data-pm-slice="0 0 []">Nadpis</h2>' +
        '<p class="editor-paragraph-block" data-id="b">První odstavec</p>' +
        '<p class="editor-paragraph-block" data-id="c"></p>' +
        '<p class="editor-paragraph-block" data-id="d" style="text-align: center">Na střed</p>'
    );

    expect(text).toBe("Nadpis\nPrvní odstavec\n\nNa střed");
    expect(html).toBe(
      "<h2>Nadpis</h2>" +
        '<p style="margin-top: 0; margin-bottom: 6pt">První odstavec</p>' +
        '<p style="margin-top: 0; margin-bottom: 6pt"><br></p>' +
        '<p style="text-align: center; margin-top: 0; margin-bottom: 6pt">Na střed</p>'
    );
  });

  it("writes user mentions as @name and drops mentions it cannot resolve", () => {
    const { html, text } = serialize(
      '<p data-pm-slice="1 1 []">Ahoj <mention-component id="m1" entity_identifier="user-1" entity_name="user_mention">' +
        '</mention-component>, a <mention-component id="m2" entity_identifier="user-2" entity_name="user_mention">' +
        "</mention-component>!</p>"
    );

    expect(text).toBe("Ahoj @vojtech, a !");
    expect(html).toBe("Ahoj @vojtech, a !");
  });

  it("turns editor colours into inline hex colours", () => {
    const { html, text } = serialize(
      '<p data-pm-slice="1 1 []"><span data-text-color="peach">červený</span> ' +
        '<span data-background-color="var(--editor-colors-green-background)">zelené pozadí</span> ' +
        '<span data-text-color="#123456" style="color: #123456">vlastní</span> ' +
        '<span data-text-color="var(--unknown)">neznámý</span></p>'
    );

    expect(text).toBe("červený zelené pozadí vlastní neznámý");
    expect(html).toBe(
      '<span style="color: #ff5b59">červený</span> ' +
        '<span style="background-color: #c3f0de">zelené pozadí</span> ' +
        '<span style="color: #123456">vlastní</span> neznámý'
    );
  });

  it("writes nested and numbered lists with bullets and numbers in plain text", () => {
    const { html, text } = serialize(
      '<ul class="list-disc pl-7 tight" data-tight="true" data-id="l1" data-pm-slice="0 0 []">' +
        '<li class="not-prose space-y-2" data-id="i1"><p class="editor-paragraph-block">První</p>' +
        '<ul class="list-disc" data-tight="true"><li><p>Vnořená</p><ul><li><p>Hlubší</p></li></ul></li></ul></li>' +
        "<li><p>Druhá</p><p>Pokračování</p></li></ul>" +
        '<ol class="list-decimal" start="3"><li><p>Třetí</p></li><li><p>Čtvrtá</p></li></ol>'
    );

    expect(text).toBe(
      ["• První", "    ◦ Vnořená", "        ▪ Hlubší", "• Druhá", "    Pokračování", "3. Třetí", "4. Čtvrtá"].join("\n")
    );
    const output = parse(html);
    expect(output.querySelector("ol")?.getAttribute("start")).toBe("3");
    expect(output.querySelectorAll("ul ul ul li")).toHaveLength(1);
    expect(output.querySelector("li > p")?.getAttribute("style")).toBe("margin-top: 0; margin-bottom: 0");
    expect(html).not.toMatch(/class=|data-/);
  });

  it("turns checklists into ☐ / ☑ paragraphs indented by nesting level", () => {
    const { html, text } = serialize(
      '<ul class="not-prose pl-2 space-y-2" data-id="t" data-type="taskList" data-pm-slice="0 0 []">' +
        '<li class="relative" data-id="t1" data-checked="true" data-type="taskItem"><label>' +
        '<input type="checkbox" checked="checked"><span></span></label><div><p class="editor-paragraph-block">Hotovo</p>' +
        "</div></li>" +
        '<li class="relative" data-checked="false" data-type="taskItem"><label><input type="checkbox"><span></span>' +
        '</label><div><p class="editor-paragraph-block">Zbývá</p><ul data-type="taskList">' +
        '<li data-checked="false" data-type="taskItem"><label><input type="checkbox"><span></span></label><div>' +
        "<p>Podúkol</p></div></li></ul></div></li></ul>"
    );

    expect(text).toBe("☑ Hotovo\n☐ Zbývá\n    ☐ Podúkol");
    expect(html).toBe(
      '<p style="margin-top: 0; margin-bottom: 6pt">☑ Hotovo</p>' +
        '<p style="margin-top: 0; margin-bottom: 6pt">☐ Zbývá</p>' +
        '<p style="margin-left: 18pt; margin-top: 0; margin-bottom: 6pt">☐ Podúkol</p>'
    );
  });

  it("writes tables as tab-separated text and as bordered HTML tables with resolved cell colours", () => {
    const { html, text } = serialize(
      '<table data-id="tb" data-pm-slice="1 1 -2 []"><tbody><tr style="">' +
        '<th colspan="1" rowspan="1" colwidth="150" background="none" style=""><p class="editor-paragraph-block">Název</p></th>' +
        '<th colspan="1" rowspan="1" colwidth="150"><p>Stav</p></th></tr>' +
        '<tr><td colspan="2" rowspan="1" colwidth="150,150" background="var(--editor-colors-peach-background)" ' +
        'style="background-color: var(--editor-colors-peach-background);"><p>Spojená buňka</p></td></tr></tbody></table>'
    );

    expect(text).toBe("Název\tStav\nSpojená buňka");
    const output = parse(html);
    expect(output.querySelector("table")?.getAttribute("style")).toBe("border-collapse: collapse");
    expect(output.querySelector("th")?.getAttribute("style")).toBe(
      "border: 1px solid #d0d0d0; padding: 4px 8px; vertical-align: top; text-align: left; font-weight: bold"
    );
    const cell = output.querySelector("td");
    expect(cell?.getAttribute("colspan")).toBe("2");
    expect(cell?.hasAttribute("rowspan")).toBe(false);
    expect(cell?.getAttribute("style")).toBe(
      "background-color: #ffd5d7; border: 1px solid #d0d0d0; padding: 4px 8px; vertical-align: top"
    );
    expect(html).not.toMatch(/colwidth|background=|var\(/);
  });

  it("copies code verbatim, without Markdown fences or backticks", () => {
    const { html, text } = serialize(
      '<pre class="" data-id="c" data-pm-slice="0 0 []"><code class="language-ts">const a = 1;\nconsole.log(a);</code></pre>' +
        '<p>Použij <code class="rounded-sm bg-layer-3" spellcheck="false">npm i</code>.</p>'
    );

    expect(text).toBe("const a = 1;\nconsole.log(a);\nPoužij npm i.");
    const output = parse(html);
    expect(output.querySelector("pre")?.getAttribute("style")).toContain(
      "font-family: Consolas, 'Courier New', monospace"
    );
    expect(output.querySelector("pre > code")?.textContent).toBe("const a = 1;\nconsole.log(a);");
    expect(output.querySelector("p > code")?.getAttribute("style")).toBe(
      "font-family: Consolas, 'Courier New', monospace"
    );
  });

  it("turns callouts into quotes with their emoji and the horizontal rule into <hr>", () => {
    const { html, text } = serialize(
      '<div data-id="x" id="x" data-emoji-unicode="128161" data-logo-in-use="emoji" data-background="peach" ' +
        'data-block-type="callout-component" data-pm-slice="0 0 []"><p>Pozor na<br>termín</p></div>' +
        '<div class="py-4 border-strong-1" data-type="horizontalRule"><div></div></div><p>Za čarou</p>'
    );

    expect(text).toBe("💡 Pozor na\ntermín\nZa čarou");
    expect(html).toBe(
      '<blockquote><p style="margin-top: 0; margin-bottom: 6pt">💡 Pozor na<br>termín</p></blockquote>' +
        '<hr><p style="margin-top: 0; margin-bottom: 6pt">Za čarou</p>'
    );
  });

  it("embeds uploaded images it has a data URI for, keeps public images and leaves images out of plain text", () => {
    const imageSources = new Map([
      ["asset-1", "data:image/png;base64,AAAA"],
      ["/uploads/b.png", "data:image/png;base64,BBBB"],
    ]);
    const { html, text } = serialize(
      '<image-component data-id="i" src="asset-1" width="400px" height="300px" id="i" aspectratio="1.33" ' +
        'alignment="center" status="uploaded"></image-component>' +
        '<image-component src="asset-2" width="35%"></image-component>' +
        '<img data-id="j" src="https://example.com/a.png" alt="obr" width="35%" alignment="left">' +
        '<img src="/uploads/b.png" width="200px"><img src="/uploads/c.png"><p>Text</p>',
      imageSources
    );

    expect(text).toBe("Text");
    expect(html).toBe(
      '<p style="text-align: center; margin-top: 0; margin-bottom: 6pt">' +
        '<img src="data:image/png;base64,AAAA" alt="" width="400" height="300"></p>' +
        '<img src="https://example.com/a.png" alt="obr">' +
        '<img src="data:image/png;base64,BBBB" width="200">' +
        // a relative URL is resolved against the page
        `<img src="${new URL("/uploads/c.png", document.baseURI).href}">` +
        '<p style="margin-top: 0; margin-bottom: 6pt">Text</p>'
    );
  });

  it("drops unsafe links, unknown custom elements and Plane-only attributes", () => {
    const { html, text } = serialize(
      '<p data-id="p" data-pm-slice="0 0 []"><a href="javascript:alert(1)">klik</a> a <a href="">nic</a></p>' +
        '<issue-embed-component entity_identifier="x"></issue-embed-component>'
    );

    expect(text).toBe("klik a nic");
    expect(html).toBe('<p style="margin-top: 0; margin-bottom: 6pt">klik a nic</p>');
  });
});
