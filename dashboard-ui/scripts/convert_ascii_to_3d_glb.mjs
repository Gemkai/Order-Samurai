import * as THREE from "three"
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js"
import fs from "node:fs"
import path from "node:path"

globalThis.FileReader = class FileReader {
  readAsArrayBuffer(blob) {
    Promise.resolve(blob.arrayBuffer ? blob.arrayBuffer() : blob).then((buf) => {
      this.result = buf
      if (this.onload) this.onload({ target: this })
    })
  }
  readAsDataURL(blob) {
    Promise.resolve(blob.arrayBuffer ? blob.arrayBuffer() : blob).then((buf) => {
      const base64 = Buffer.from(buf).toString("base64")
      this.result = `data:application/octet-stream;base64,${base64}`
      if (this.onload) this.onload({ target: this })
    })
  }
}

const modelsDir = path.resolve("public/models")
if (!fs.existsSync(modelsDir)) {
  fs.mkdirSync(modelsDir, { recursive: true })
}

// 1. Read ASCII art strings from AsciiBackground.tsx
const asciiBgPath = path.resolve("src/components/AsciiBackground.tsx")
const code = fs.readFileSync(asciiBgPath, "utf-8")

function extractArt(varName) {
  const match = code.match(new RegExp(`const ${varName} = \`([\\s\\S]*?)\``))
  return match ? match[1] : ""
}

const OVERVIEW_ART = extractArt("OVERVIEW_ART")
const BOW_ART = extractArt("BOW_ART")
const SWORD_ART = extractArt("SWORD_ART")
const BRUSH_ART = extractArt("BRUSH_ART")
const ARTS_ART = extractArt("ARTS_ART")

// Helper: Convert Braille char to 2x4 bitmask
function getBrailleDots(ch) {
  const code = ch.charCodeAt(0)
  if (code >= 0x2800 && code <= 0x28ff) {
    const bits = code - 0x2800
    const dots = []
    if (bits & 1) dots.push([0, 0])
    if (bits & 2) dots.push([0, 1])
    if (bits & 4) dots.push([0, 2])
    if (bits & 8) dots.push([1, 0])
    if (bits & 16) dots.push([1, 1])
    if (bits & 32) dots.push([1, 2])
    if (bits & 64) dots.push([0, 3])
    if (bits & 128) dots.push([1, 3])
    return dots
  }
  return null
}

// Convert 2D ASCII art into 3D Volumetric Radial Scene
function asciiArtTo3DScene(artText, colorHex, depthScale = 1.0) {
  const scene = new THREE.Scene()
  const lines = artText.split("\n").filter((l) => l.trim().length > 0)
  if (lines.length === 0) return scene

  const maxRows = lines.length
  const activePoints = []

  for (let r = 0; r < maxRows; r++) {
    const line = lines[r] || ""
    for (let c = 0; c < line.length; c++) {
      const ch = line[c]
      if (ch === " " || ch === "⠀") continue

      const dots = getBrailleDots(ch)
      if (dots && dots.length > 0) {
        for (const [dx, dy] of dots) {
          const px = c * 2 + dx
          const py = r * 4 + dy
          activePoints.push({ px, py, weight: 1.0 })
        }
      } else {
        const weight = ch === "█" ? 1.0 : ch === "▓" ? 0.75 : ch === "▒" ? 0.5 : 0.25
        activePoints.push({ px: c * 2, py: r * 4, weight })
        activePoints.push({ px: c * 2 + 1, py: r * 4 + 1, weight })
      }
    }
  }

  if (activePoints.length === 0) return scene

  const minPx = Math.min(...activePoints.map((p) => p.px))
  const maxPx = Math.max(...activePoints.map((p) => p.px))
  const minPy = Math.min(...activePoints.map((p) => p.py))
  const maxPy = Math.max(...activePoints.map((p) => p.py))

  const width = maxPx - minPx || 1
  const height = maxPy - minPy || 1
  const aspect = width / height

  const rowMap = new Map()
  for (const pt of activePoints) {
    const entry = rowMap.get(pt.py)
    if (!entry) {
      rowMap.set(pt.py, { minX: pt.px, maxX: pt.px })
    } else {
      entry.minX = Math.min(entry.minX, pt.px)
      entry.maxX = Math.max(entry.maxX, pt.px)
    }
  }

  const group = new THREE.Group()
  const mat = new THREE.MeshStandardMaterial({
    color: colorHex,
    roughness: 0.35,
    metalness: 0.65,
  })

  const cubeGeo = new THREE.BoxGeometry(0.065, 0.065, 0.065)

  for (const pt of activePoints) {
    const rowInfo = rowMap.get(pt.py) || { minX: pt.px, maxX: pt.px }
    const rowWidth = Math.max(1, rowInfo.maxX - rowInfo.minX)
    const rowCenter = (rowInfo.minX + rowInfo.maxX) / 2
    const rowRadius = rowWidth / 2

    const u = Math.max(-1, Math.min(1, (pt.px - rowCenter) / (rowRadius || 1)))
    const radiusFactor = Math.sqrt(Math.max(0, 1 - u * u))
    const zMax = (rowRadius / width) * 2.5 * radiusFactor * depthScale

    const nx = ((pt.px - minPx) / width - 0.5) * 2
    const ny = (0.5 - (pt.py - minPy) / height) * 2

    const posX = nx * aspect * 1.5
    const posY = ny * 1.5

    // Front shell
    const mFront = new THREE.Mesh(cubeGeo, mat)
    mFront.position.set(posX, posY, zMax)
    group.add(mFront)

    // Back shell
    const mBack = new THREE.Mesh(cubeGeo, mat)
    mBack.position.set(posX, posY, -zMax)
    group.add(mBack)

    // Core fill for full 360° solid volume
    if (zMax > 0.08) {
      const steps = Math.min(3, Math.floor(zMax / 0.08))
      for (let s = 1; s < steps; s++) {
        const frac = (s / steps) * 2 - 1
        const mCore = new THREE.Mesh(cubeGeo, mat)
        mCore.position.set(posX, posY, zMax * frac)
        group.add(mCore)
      }
    }
  }

  scene.add(group)
  return scene
}

function saveSceneToGLTF(scene, filename) {
  const exporter = new GLTFExporter()
  exporter.parse(
    scene,
    (gltf) => {
      const json = JSON.stringify(gltf, null, 2)
      fs.writeFileSync(path.join(modelsDir, filename), json)
      console.log(`Exported 3D ASCII Model ${filename} (${json.length} bytes)`)
    },
    (err) => console.error(`Error exporting ${filename}:`, err),
    { binary: false }
  )
}

saveSceneToGLTF(asciiArtTo3DScene(OVERVIEW_ART, 0x38bdf8, 1.0), "helmet.glb")
saveSceneToGLTF(asciiArtTo3DScene(BOW_ART, 0xf59e0b, 0.8), "bow.glb")
saveSceneToGLTF(asciiArtTo3DScene(SWORD_ART, 0xef4444, 1.2), "sword.glb")
saveSceneToGLTF(asciiArtTo3DScene(BRUSH_ART, 0xec4899, 0.9), "brush.glb")
saveSceneToGLTF(asciiArtTo3DScene(ARTS_ART, 0x4ade80, 1.0), "arts.glb")
