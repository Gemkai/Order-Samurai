/**
 * Generate authentic samurai-themed 3D models as glTF JSON files.
 * Uses Three.js pure procedural geometry (no downloads, no samples).
 * Output: public/models/{helmet,sword,bow,brush,arts}.glb (glTF JSON format)
 */
import * as THREE from "three"
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const modelsDir = path.resolve(__dirname, "../public/models")

if (!fs.existsSync(modelsDir)) {
  fs.mkdirSync(modelsDir, { recursive: true })
}

// FileReader polyfill — GLTFExporter uses readAsDataURL().onloadend for buffer URI even in JSON mode
globalThis.FileReader = class NodeFileReader {
  constructor() {
    this.result = null
    this.onload = null
    this.onloadend = null
    this.onerror = null
  }

  readAsArrayBuffer(blob) {
    Promise.resolve().then(async () => {
      try {
        const buf = blob?.arrayBuffer ? await blob.arrayBuffer() : blob
        this.result = buf instanceof ArrayBuffer ? buf : buf.buffer
        if (this.onload)    this.onload({ target: this })
        if (this.onloadend) this.onloadend({ target: this })
      } catch (e) {
        if (this.onerror) this.onerror(e)
      }
    })
  }

  readAsDataURL(blob) {
    Promise.resolve().then(async () => {
      try {
        const buf = blob?.arrayBuffer ? await blob.arrayBuffer() : blob
        const bytes = Buffer.from(buf instanceof ArrayBuffer ? buf : await buf)
        const mime = blob?.type || "application/octet-stream"
        this.result = `data:${mime};base64,${bytes.toString("base64")}`
        if (this.onload)    this.onload({ target: this })
        if (this.onloadend) this.onloadend({ target: this })
      } catch (e) {
        if (this.onerror) this.onerror(e)
      }
    })
  }
}


// Utility: Export a scene as glTF JSON (synchronous callback — no FileReader needed)
function exportScene(scene, filename) {
  return new Promise((resolve, reject) => {
    const exporter = new GLTFExporter()
    // binary: false → callback is called synchronously with a plain JS object
    exporter.parse(
      scene,
      (gltf) => {
        try {
          const outPath = path.join(modelsDir, filename)
          const json = JSON.stringify(gltf)
          fs.writeFileSync(outPath, json, "utf8")
          console.log(`✅  ${filename}  (glTF JSON, ${(json.length / 1024).toFixed(1)} KB)`)
          resolve()
        } catch (e) {
          reject(e)
        }
      },
      (err) => {
        console.error(`❌  Error exporting ${filename}:`, err)
        reject(err)
      },
      { binary: false }
    )
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. HELMET — Kabuto Samurai War Helmet
// ─────────────────────────────────────────────────────────────────────────────
function buildHelmet() {
  const scene = new THREE.Scene()
  const steel = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.25, metalness: 0.85 })
  const gold  = new THREE.MeshStandardMaterial({ color: 0xfacc15, roughness: 0.2,  metalness: 0.9  })
  const red   = new THREE.MeshStandardMaterial({ color: 0xd97706, roughness: 0.4,  metalness: 0.6  })
  const cyan  = new THREE.MeshStandardMaterial({ color: 0x38bdf8, roughness: 0.3,  metalness: 0.8  })

  // Bowl (Hachi)
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(1.2, 32, 24, 0, Math.PI * 2, 0, Math.PI * 0.52), steel))

  // 16 Ribs (Su-tate)
  for (let i = 0; i < 16; i++) {
    const rib = new THREE.Mesh(new THREE.TorusGeometry(1.21, 0.02, 8, 16, Math.PI * 0.5), gold)
    rib.rotation.set(Math.PI * 0.5, (i / 16) * Math.PI * 2, 0)
    scene.add(rib)
  }

  // Visor (Maezashi)
  const visor = new THREE.Mesh(
    new THREE.CylinderGeometry(1.25, 1.3, 0.15, 32, 1, true, -Math.PI * 0.35, Math.PI * 0.7),
    steel
  )
  visor.rotation.x = -0.3
  visor.position.set(0, 0.3, 0.2)
  scene.add(visor)

  // Kuwagata Horns
  const hornShape = new THREE.Shape()
  hornShape.moveTo(0, 0)
  hornShape.quadraticCurveTo(0.4, 1.2, 0.9, 2.2)
  hornShape.quadraticCurveTo(0.6, 2.4, 0.3, 2.2)
  hornShape.quadraticCurveTo(0.1, 1.2, 0, 0)
  const hornGeo = new THREE.ExtrudeGeometry(hornShape, { depth: 0.05, bevelEnabled: true, bevelSize: 0.02, bevelThickness: 0.02, bevelSegments: 2 })
  for (const side of [-1, 1]) {
    const horn = new THREE.Mesh(hornGeo, gold)
    horn.scale.set(side * 0.7, 0.7, 0.7)
    horn.position.set(side * 0.1, 0.9, 1.1)
    horn.rotation.set(0, side * -0.2, side * -0.3)
    scene.add(horn)
  }

  // Maedate Crest
  const emblem = new THREE.Mesh(new THREE.CylinderGeometry(0.35, 0.35, 0.06, 32), cyan)
  emblem.rotation.x = Math.PI / 2
  emblem.position.set(0, 1.0, 1.15)
  scene.add(emblem)

  // Menpo Face Mask
  const mask = new THREE.Mesh(
    new THREE.CylinderGeometry(0.85, 0.65, 0.9, 32, 1, true, -Math.PI * 0.38, Math.PI * 0.76),
    steel
  )
  mask.position.set(0, -0.35, 0.25)
  scene.add(mask)

  // Mustache (Hige)
  const stache = new THREE.Mesh(new THREE.TorusGeometry(0.35, 0.04, 8, 16, Math.PI), gold)
  stache.rotation.x = Math.PI * 0.6
  stache.position.set(0, -0.2, 0.95)
  scene.add(stache)

  // 4-Tiered Shikoro Neck Guard
  for (let i = 0; i < 4; i++) {
    const plate = new THREE.Mesh(
      new THREE.CylinderGeometry(1.28 + i * 0.12, 1.34 + i * 0.12, 0.18, 32, 1, true, Math.PI * 0.15, Math.PI * 1.7),
      i % 2 === 0 ? steel : red
    )
    plate.position.set(0, -i * 0.22, -0.05)
    scene.add(plate)
  }

  return scene
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. SWORD — Katana with Sori curve, Tsuba, Tsuka, Kashira
// ─────────────────────────────────────────────────────────────────────────────
function buildSword() {
  const scene = new THREE.Scene()
  const bladeMat  = new THREE.MeshStandardMaterial({ color: 0xf0f4f8, roughness: 0.08, metalness: 0.97 })
  const goldMat   = new THREE.MeshStandardMaterial({ color: 0xfacc15, roughness: 0.2,  metalness: 0.9  })
  const wrapMat   = new THREE.MeshStandardMaterial({ color: 0xef4444, roughness: 0.55, metalness: 0.2  })
  const raySkin   = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.75, metalness: 0.05 })

  // ── Blade: swept diamond profile along Sori curve ──
  const bladeCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0,    -0.8, 0),
    new THREE.Vector3(0.05,  0.4, 0),
    new THREE.Vector3(0.18,  1.8, 0),
    new THREE.Vector3(0.35,  3.2, 0),
    new THREE.Vector3(0.42,  4.0, 0), // kissaki tip
  ])
  const bladeShape = new THREE.Shape()
  bladeShape.moveTo(0,     -0.035)
  bladeShape.lineTo(0.115, -0.008)
  bladeShape.lineTo(0.135,  0)
  bladeShape.lineTo(0.115,  0.008)
  bladeShape.lineTo(0,      0.035)
  bladeShape.lineTo(-0.018, 0)
  bladeShape.closePath()
  const blade = new THREE.Mesh(
    new THREE.ExtrudeGeometry(bladeShape, { steps: 80, extrudePath: bladeCurve }),
    bladeMat
  )
  scene.add(blade)

  // ── Habaki (blade collar above tsuba) ──
  const habaki = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.18, 0.25, 16), goldMat)
  habaki.position.set(0, -0.82, 0)
  scene.add(habaki)

  // ── Tsuba (oval handguard) ──
  const tsuba = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 0.62, 0.09, 32), goldMat)
  tsuba.scale.set(1.0, 1.0, 0.72)
  tsuba.position.set(0, -1.05, 0)
  scene.add(tsuba)

  // ── Tsuka (handle) with Samegawa ray-skin ──
  const handle = new THREE.Mesh(new THREE.CylinderGeometry(0.155, 0.135, 1.55, 16), raySkin)
  handle.position.set(-0.04, -1.85, 0)
  scene.add(handle)

  // ── Ito (handle wrap) diamond rings ──
  for (let y = -1.15; y > -2.55; y -= 0.19) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.155, 0.028, 8, 16), wrapMat)
    ring.rotation.set(Math.PI / 2, 0.32, 0)
    ring.position.set(-0.04, y, 0)
    scene.add(ring)
  }

  // ── Kashira (pommel) ──
  const pommel = new THREE.Mesh(new THREE.CylinderGeometry(0.155, 0.135, 0.14, 16), goldMat)
  pommel.position.set(-0.05, -2.67, 0)
  scene.add(pommel)

  return scene
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. BOW — Asymmetrical Yumi bow + Ya arrow
// ─────────────────────────────────────────────────────────────────────────────
function buildBow() {
  const scene = new THREE.Scene()
  const bamboo = new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: 0.35, metalness: 0.45 })
  const rattan = new THREE.MeshStandardMaterial({ color: 0x78350f, roughness: 0.75 })
  const string = new THREE.MeshStandardMaterial({ color: 0xfaf5eb, roughness: 0.1  })
  const steel  = new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.18, metalness: 0.92 })

  // Asymmetrical Yumi stave — longer upper limb
  const bowCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0,    2.8, 0),
    new THREE.Vector3(0.7,  1.6, 0),
    new THREE.Vector3(0.55, 0.3, 0),
    new THREE.Vector3(0.28, -0.5, 0),
    new THREE.Vector3(0.72, -1.7, 0),
    new THREE.Vector3(0,   -2.5, 0),
  ])
  scene.add(new THREE.Mesh(new THREE.TubeGeometry(bowCurve, 80, 0.1, 12, false), bamboo))

  // Rattan bindings
  for (let t = 0.08; t <= 0.92; t += 0.09) {
    const pt = bowCurve.getPoint(t)
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.11, 0.022, 8, 16), rattan)
    ring.position.copy(pt)
    ring.rotation.x = Math.PI / 2
    scene.add(ring)
  }

  // Bowstring — straight line between tip points
  scene.add(new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, 5.3, 8), string))

  // Arrow shaft
  const arrow = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 3.6, 12), rattan)
  arrow.rotation.z = -Math.PI / 2
  arrow.position.set(0.55, -0.35, 0)
  scene.add(arrow)

  // Arrowhead
  const headShape = new THREE.Shape()
  headShape.moveTo(0,    0)
  headShape.lineTo(0.14, 0.48)
  headShape.lineTo(0,    0.38)
  headShape.lineTo(-0.14, 0.48)
  headShape.closePath()
  const head = new THREE.Mesh(
    new THREE.ExtrudeGeometry(headShape, { depth: 0.03, bevelEnabled: true, bevelSize: 0.01, bevelThickness: 0.01 }),
    steel
  )
  head.rotation.z = -Math.PI / 2
  head.position.set(2.45, -0.35, -0.015)
  scene.add(head)

  // Fletching (3 vanes)
  for (let a = 0; a < 3; a++) {
    const vane = new THREE.Mesh(
      new THREE.BoxGeometry(0.38, 0.02, 0.18),
      new THREE.MeshStandardMaterial({ color: 0xfca5a5, roughness: 0.6 })
    )
    vane.position.set(-1.3, -0.35, 0)
    vane.rotation.set(0, (a * Math.PI * 2) / 3, 0)
    scene.add(vane)
  }

  return scene
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. BRUSH — Japanese Fude calligraphy brush
// ─────────────────────────────────────────────────────────────────────────────
function buildBrush() {
  const scene = new THREE.Scene()
  const handle  = new THREE.MeshStandardMaterial({ color: 0xfce7f3, roughness: 0.4,  metalness: 0.25 })
  const ferrule = new THREE.MeshStandardMaterial({ color: 0xec4899, roughness: 0.22, metalness: 0.82 })
  const hair    = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.82, metalness: 0.05 })
  const lacquer = new THREE.MeshStandardMaterial({ color: 0x831843, roughness: 0.15, metalness: 0.7  })

  // Tapered bamboo shaft
  scene.add(new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.22, 4.0, 32), handle))

  // Bamboo node rings along shaft
  for (const y of [-0.7, 0.15, 1.0, 1.85, 2.55]) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.195, 0.032, 10, 32), ferrule)
    ring.rotation.x = Math.PI / 2
    ring.position.y = y
    scene.add(ring)
  }

  // Lacquer accent bands
  for (const y of [1.45, 2.15]) {
    const band = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.18, 0.065, 32), lacquer)
    band.position.y = y
    scene.add(band)
  }

  // Metal ferrule cap (transition to hair)
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.275, 0.55, 32), ferrule)
  cap.position.y = -2.05 + 0.6   // = -1.45 top of hair region
  scene.add(cap)

  // Conical hair tuft (teardrop tip)
  const tuft = new THREE.Mesh(new THREE.ConeGeometry(0.36, 1.5, 32), hair)
  tuft.rotation.x = Math.PI
  tuft.position.y = -2.65
  scene.add(tuft)

  // Fine tip point
  const tip = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.4, 16), hair)
  tip.rotation.x = Math.PI
  tip.position.y = -3.45
  scene.add(tip)

  return scene
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. ARTS — Japanese Pagoda Castle (Shiro)
// ─────────────────────────────────────────────────────────────────────────────
function buildArts() {
  const scene   = new THREE.Scene()
  const stone   = new THREE.MeshStandardMaterial({ color: 0x475569, roughness: 0.88 })
  const plaster = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.3  })
  const roof    = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.35, metalness: 0.65 })
  const gold    = new THREE.MeshStandardMaterial({ color: 0xfacc15, roughness: 0.18, metalness: 0.92 })

  // Ishigaki stone base (angled battered walls — quad prism)
  const base = new THREE.Mesh(new THREE.CylinderGeometry(2.1, 3.1, 1.6, 4), stone)
  base.rotation.y = Math.PI / 4
  base.position.set(0, -2.0, 0)
  scene.add(base)

  const levels = [
    { w: 2.6,  h: 1.1,  roofR: 3.4, roofH: 0.9,  y: -0.8 },
    { w: 1.9,  h: 1.0,  roofR: 2.6, roofH: 0.85, y:  0.7 },
    { w: 1.35, h: 0.85, roofR: 1.9, roofH: 0.8,  y:  2.0 },
    { w: 0.85, h: 0.75, roofR: 1.3, roofH: 0.72, y:  3.1 },
  ]

  for (const lv of levels) {
    // Whitewashed walls
    const wall = new THREE.Mesh(new THREE.BoxGeometry(lv.w, lv.h, lv.w), plaster)
    wall.position.set(0, lv.y, 0)
    scene.add(wall)
    // Flared irimoya roof
    const r = new THREE.Mesh(new THREE.ConeGeometry(lv.roofR, lv.roofH, 4), roof)
    r.rotation.y = Math.PI / 4
    r.position.y = lv.y + lv.h * 0.5 + lv.roofH * 0.5
    scene.add(r)
  }

  // Sorin spire on top
  const spireBase = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.25, 0.7, 16), gold)
  spireBase.position.y = 4.55
  scene.add(spireBase)

  const spirePeak = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.55, 16), gold)
  spirePeak.position.y = 5.18
  scene.add(spirePeak)

  // Shachihoko finials on level-1 roof corners
  for (const [sx, sz] of [[1, 1], [-1, 1], [1, -1], [-1, -1]]) {
    const fin = new THREE.Mesh(new THREE.ConeGeometry(0.09, 0.32, 8), gold)
    fin.position.set(sx * 1.6, 0.25, sz * 1.6)
    scene.add(fin)
  }

  return scene
}


// ─────────────────────────────────────────────────────────────────────────────
// Run all exports in sequence
// ─────────────────────────────────────────────────────────────────────────────
async function main() {
  console.log("🗡️  Generating authentic samurai 3D models...\n")
  await exportScene(buildHelmet(), "helmet.glb")
  await exportScene(buildSword(),  "sword.glb")
  await exportScene(buildBow(),    "bow.glb")
  await exportScene(buildBrush(),  "brush.glb")
  await exportScene(buildArts(),   "arts.glb")
  console.log("\n✅  All 5 models written to public/models/")
}

// Keep Node alive until async FileReader microtasks have all drained
const keepAlive = setInterval(() => {}, 50)

main()
  .then(() => { clearInterval(keepAlive); process.exit(0) })
  .catch((err) => {
    console.error("Fatal error during export:", err)
    clearInterval(keepAlive)
    process.exit(1)
  })
