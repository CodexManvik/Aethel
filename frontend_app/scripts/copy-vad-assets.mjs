// Copies the voice-activity model, its audio worklet and onnxruntime's wasm into public/vad/, where the call view
// loads them from (the app's own origin: the CSP allows nothing else). Run before dev and build.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const vadDist = join(root, "node_modules", "@ricky0123", "vad-web", "dist");
const ortDist = join(root, "node_modules", "onnxruntime-web", "dist");  // a direct dependency, pinned like vad-web

export const VAD_ASSETS = [
  [vadDist, "silero_vad_v5.onnx"],
  [vadDist, "vad.worklet.bundle.min.js"],
  [ortDist, "ort-wasm-simd-threaded.wasm"],
  [ortDist, "ort-wasm-simd-threaded.mjs"],
];

export function copyVadAssets(target = join(root, "public", "vad")) {
  mkdirSync(target, { recursive: true });
  for (const [dir, name] of VAD_ASSETS) copyFileSync(join(dir, name), join(target, name));
  return VAD_ASSETS.map(([, name]) => name);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  console.log(`copied ${copyVadAssets().join(", ")} into public/vad/`);
}
