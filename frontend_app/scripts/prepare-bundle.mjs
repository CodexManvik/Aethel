// Prepares what the installer carries besides the web UI (spec 2026-10-19 §I1), into src-tauri/payload/:
//   uv/uv.exe, uv/uvx.exe   Astral uv, pinned, checked against its SHA-256 (sets up the backend's Python on first run)
//   backend/aethel/         the backend's source (no __pycache__)
//   requirements/           core.txt (the hashed lock) and local-images.txt
// Run by `pnpm bundle:prepare` (and before `tauri build`). The payload folder is generated: never edit it.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, "..");
const repo = join(app, "..");
const payload = join(app, "src-tauri", "payload");
const cache = join(app, "src-tauri", ".payload-cache");

export const UV = {
  version: "0.12.20", // 2026-09-28
  url: "https://github.com/astral-sh/uv/releases/download/0.12.20/uv-x86_64-pc-windows-msvc.zip",
  sha256: "95f9bc30fbb3574d276e28ac4a6de932d25153645853d13da8c21eec3bc88d06",
};

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

async function fetchUv() {
  mkdirSync(cache, { recursive: true });
  const zip = join(cache, `uv-${UV.version}.zip`);
  if (!existsSync(zip) || sha256(readFileSync(zip)) !== UV.sha256) {
    console.log(`downloading uv ${UV.version}`);
    const res = await fetch(UV.url, { redirect: "follow" });
    if (!res.ok) throw new Error(`uv download failed: ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    if (sha256(buf) !== UV.sha256) throw new Error("uv didn't match its pinned SHA-256; refused");
    writeFileSync(zip, buf);
  }
  const out = join(payload, "uv");
  mkdirSync(out, { recursive: true });
  execFileSync("tar", ["-xf", zip, "-C", out]); // Windows 10+ tar reads zip
  for (const exe of ["uv.exe", "uvx.exe"]) if (!existsSync(join(out, exe))) throw new Error(`${exe} missing from the uv zip`);
}

function copyBackend() {
  const dest = join(payload, "backend", "aethel");
  rmSync(join(payload, "backend"), { recursive: true, force: true });
  cpSync(join(repo, "backend", "aethel"), dest, {
    recursive: true,
    filter: (src) => !/__pycache__|\.pyc$|\.playwright-mcp/.test(src),
  });
  const reqs = join(payload, "requirements");
  mkdirSync(reqs, { recursive: true });
  for (const f of ["core.txt", "local-images.txt"]) {
    const from = join(repo, "backend", "requirements", f);
    if (!existsSync(from)) throw new Error(`${f} is missing: generate the lock first (see backend/requirements/core.in)`);
    cpSync(from, join(reqs, f));
  }
}

const only = process.argv[2];
if (!only || only === "uv") await fetchUv();
if (!only || only === "backend") copyBackend();
console.log(`payload ready in ${payload}`);
