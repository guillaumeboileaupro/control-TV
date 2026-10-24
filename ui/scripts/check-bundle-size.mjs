import { readdir, stat } from "node:fs/promises";
import { join } from "node:path";

const assets = new URL("../dist/assets/", import.meta.url);
const javascript = (await readdir(assets)).filter((name) => name.endsWith(".js"));
const sizes = await Promise.all(javascript.map((name) => stat(join(assets.pathname, name))));
const totalBytes = sizes.reduce((total, entry) => total + entry.size, 0);
// Guards against bundling the complete Simple Icons catalogue (megabytes), not ordinary features:
// raised from 64 KiB for the Screen to TV card (D3: 62.0 kB before it, 69.0 kB with it), then to
// 96 KiB for the display manager (85.8 kB with it), local modules only in both cases.
const limitBytes = 96 * 1024;

if (totalBytes > limitBytes) {
  throw new Error(`frontend JavaScript bundle is ${totalBytes} bytes; limit is ${limitBytes}`);
}

console.log(`frontend JavaScript bundle: ${totalBytes} / ${limitBytes} bytes`);
