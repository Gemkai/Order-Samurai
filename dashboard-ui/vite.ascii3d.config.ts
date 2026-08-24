/* Library build for the BrAIn³ media gallery's standalone ASCII-3D viewer.
 *
 * Separate from the app's own vite.config.ts on purpose: this emits a single self-contained
 * ES module (three.js bundled IN, no externals, no React) that a static HTML page can load
 * from file:// or http:// with one <script type="module">. The app build is untouched.
 *
 *   npx vite build --config vite.ascii3d.config.ts
 */
import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  // Vite's lib mode does not inject the process.env shim the app build gets, and something
  // in the dependency graph reads process.env.NODE_ENV at module scope — which threw
  // "process is not defined" in the browser and left the viewer stuck on its loading state.
  // Defining it statically also lets the bundler drop the dev-only branches.
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
    "process.env": "{}",
  },
  build: {
    lib: {
      entry: resolve(__dirname, "src/ascii3d-standalone.ts"),
      formats: ["es"],
      fileName: () => "ascii3d-viewer.js",
    },
    outDir: resolve(__dirname, "dist-ascii3d"),
    emptyOutDir: true,
    // The gallery's viewer pages are the only consumer and they load this module directly;
    // a split would mean chasing relative chunk URLs across two different origins
    // (file:// for thumbnailing, http:// for browsing). One file avoids the whole problem.
    rollupOptions: { output: { inlineDynamicImports: true } },
    target: "es2020",
    minify: true,
    sourcemap: false,
  },
});
