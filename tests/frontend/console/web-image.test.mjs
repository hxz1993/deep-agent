import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  findWebImageReference,
  renderWebImagePreview,
} from "../../../frontend/console/web-image.js";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const trajectorySource = await readFile(
  new URL("../../../frontend/console/trajectory.js", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../frontend/console/workspace.css", import.meta.url),
  "utf8",
);

test("Web Image 引用只接受公开 HTTP(S) 结构", () => {
  assert.deepEqual(
    findWebImageReference({
      result: {
        kind: "web-image-reference",
        url: "https://cdn.example.com/chart.png",
        media_type: "image/png",
        force_download: true,
      },
    }),
    {
      url: "https://cdn.example.com/chart.png",
      mediaType: "image/png",
      forceDownload: true,
    },
  );
  assert.equal(findWebImageReference({ kind: "web-image-reference", url: "javascript:alert(1)" }), null);
  assert.equal(findWebImageReference({ kind: "web-image-reference", url: "https://user:secret@example.com/a.png" }), null);
});

test("Web Image 预览懒加载、无 Referrer 且始终保留原图回退", () => {
  const html = renderWebImagePreview({
    kind: "web-image-reference",
    url: "https://cdn.example.com/chart.png?a=1&b=2",
    media_type: "image/png",
  });

  assert.match(html, /loading="lazy"/);
  assert.match(html, /referrerpolicy="no-referrer"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /图片预览加载失败/);
  assert.match(html, /a=1&amp;b=2/);
});

test("实时工具卡和轨迹详情复用同一安全预览", () => {
  assert.match(chatSource, /renderWebImagePreview\(result/);
  assert.match(trajectorySource, /renderWebImagePreview\(result/);
  assert.match(workspaceCss, /\.web-image-preview\.load-failed img\s*\{\s*display:\s*none/);
  assert.match(workspaceCss, /\.web-image-preview-fallback/);
});
