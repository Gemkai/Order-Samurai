/* ascii3d-standalone.ts — bundle entry for the BrAIn³ media gallery's 3D viewer pages.
 *
 * Why this exists: the gallery catalogues this product's .glb assets, and they are ASCII-3D
 * assets — rendering them with plain three.js materials (which the gallery did until
 * 2026-08-24) shows the geometry but loses the entire design. Rather than port the GLSL
 * pipeline into a second implementation that would drift, this exposes THE renderer
 * (createAsciiObject, in canvasui/AsciiObject.tsx) as a standalone ES module the gallery's
 * static viewer pages can load. Same shader, same defaults, one source of truth.
 *
 * Build (from dashboard-ui/):
 *   npx vite build --config vite.ascii3d.config.ts
 * Output is copied to Knowledge/dashboard/vendor/ascii3d-viewer.js — see that directory's
 * README for the regeneration contract.
 *
 * The gallery's pages must work when opened over file:// (build_thumbs.py screenshots them
 * headlessly) AND over http:// (live browsing), so the caller hands us the model as bytes,
 * not a path: Chromium blocks file://→file:// fetches, which is why the page embeds its
 * model as base64 and we turn it into a blob URL here. createAsciiObject's `src` already
 * accepts object URLs, so nothing special is needed on its side.
 */
import { createAsciiObject } from "./components/canvasui/AsciiObject";
import type { AsciiObjectInstance, AsciiObjectOptions } from "./components/canvasui/AsciiObject";

/** Studio look the gallery ships, matching the Ascii3DPlayground control defaults a viewer
 *  sees at ?gallery=1 (cell 9px / aspect 0.55 / edge 3.2 / tone 1.5 / exposure 1.2). */
const GALLERY_LOOK: AsciiObjectOptions = {
  ascii: true,
  colored: true,
  cellSize: 9,
  cellAspect: 0.55,
  edgeContrast: 3.2,
  contrast: 1.5,
  exposure: 1.2,
  invert: false,
  background: "#0e131b",
  orbit: true,
  zoom: true,
  // Framing: the renderer fits the model to `scale` scene units with the camera at
  // `cameraDistance`. Left at the component's own fov (65) rather than narrowed — a tighter
  // fov magnifies the subject and cropped the Kabuto's kuwagata horns straight off the top
  // edge. `scale` is pulled in slightly from the default 3 so every piece in the set,
  // including the tall/thin ones, keeps margin inside a thumbnail.
  scale: 2.4,
  cameraDistance: 4.2,
  fov: 65,
};

export interface MountOptions {
  canvas: HTMLCanvasElement;
  /** Raw .glb bytes, base64 — the page embeds these so file:// works. */
  modelBase64: string;
  /** Turntable + bob. Off by default so headless thumbnail captures stay deterministic. */
  animate?: boolean;
  onLoad?: () => void;
  onError?: (err: unknown) => void;
}

export function mountAsciiModel(opts: MountOptions): AsciiObjectInstance | null {
  const bin = atob(opts.modelBase64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes], { type: "model/gltf-binary" }));

  const instance = createAsciiObject(
    { canvas: opts.canvas },
    {
      ...GALLERY_LOOK,
      src: url,
      // Both idle animations are gated on the same flag: a bobbing model makes a screenshot
      // a race against the settle timer exactly the way a spinning one does.
      autoRotate: !!opts.animate,
      autoRotateSpeed: 1.5,
      floatIntensity: opts.animate ? 1.8 : 0,
      rotationIntensity: opts.animate ? 1 : 0,
      onLoad: () => {
        URL.revokeObjectURL(url); // the loader has the bytes now
        opts.onLoad?.();
      },
      onError: (err) => {
        URL.revokeObjectURL(url);
        opts.onError?.(err);
      },
    },
  );
  return instance;
}

// Surfaced for the viewer page's resize handler and for any future caller that wants to
// drive options live rather than remount.
export type { AsciiObjectInstance, AsciiObjectOptions };
