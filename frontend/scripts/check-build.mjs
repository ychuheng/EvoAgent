import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const dist = join(dirname(fileURLToPath(import.meta.url)), "../dist");
const html = readFileSync(join(dist, "index.html"), "utf8");
const assets = [...html.matchAll(/(?:src|href)="(\/ui\/assets\/[^\"]+)"/g)].map(
  ([, path]) => path,
);

assert(assets.some((path) => path.endsWith(".js")), "Missing /ui/ JavaScript asset");
assert(assets.some((path) => path.endsWith(".css")), "Missing /ui/ CSS asset");
for (const asset of assets) {
  assert(existsSync(join(dist, asset.slice("/ui/".length))), `Missing built asset: ${asset}`);
}

console.log("Built /ui/ asset paths verified");
