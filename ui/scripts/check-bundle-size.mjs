import { readdir, stat } from "node:fs/promises";
import { join } from "node:path";

const assets = new URL("../dist/assets/", import.meta.url);
const javascript = (await readdir(assets)).filter((name) => name.endsWith(".js"));
const sizes = await Promise.all(javascript.map((name) => stat(join(assets.pathname, name))));
const totalBytes = sizes.reduce((total, entry) => total + entry.size, 0);
const limitBytes = 64 * 1024;

if (totalBytes > limitBytes) {
  throw new Error(`frontend JavaScript bundle is ${totalBytes} bytes; limit is ${limitBytes}`);
}

console.log(`frontend JavaScript bundle: ${totalBytes} / ${limitBytes} bytes`);
