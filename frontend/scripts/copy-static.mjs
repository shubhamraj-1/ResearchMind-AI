import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(frontend, "dist");

mkdirSync(dist, { recursive: true });
for (const name of ["index.html", "styles.css"]) {
  copyFileSync(join(frontend, name), join(dist, name));
}
console.log("Copied index.html and styles.css to dist/");
