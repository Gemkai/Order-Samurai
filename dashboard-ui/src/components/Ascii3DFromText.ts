import * as THREE from "three"
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js"

// Helper: Convert Braille character to 2x4 sub-pixel bitmask coordinates
function getBrailleDots(ch: string): Array<[number, number]> | null {
  const code = ch.charCodeAt(0)
  if (code >= 0x2800 && code <= 0x28ff) {
    const bits = code - 0x2800
    const dots: Array<[number, number]> = []
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

export function build3DSceneFromAscii(
  artText: string,
  colorHex: number = 0x38bdf8,
  depthScale: number = 1.0
): THREE.Scene {
  const scene = new THREE.Scene()
  const lines = artText.split("\n").filter((l) => l.trim().length > 0)
  if (lines.length === 0) return scene

  const maxRows = lines.length
  const activePoints: Array<{ px: number; py: number; weight: number }> = []

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

  // Group points by row py to compute 3D radial profile & thickness envelope per cross-section
  const rowMap = new Map<number, { minX: number; maxX: number }>()
  for (const pt of activePoints) {
    const entry = rowMap.get(pt.py)
    if (!entry) {
      rowMap.set(pt.py, { minX: pt.px, maxX: pt.px })
    } else {
      entry.minX = Math.min(entry.minX, pt.px)
      entry.maxX = Math.max(entry.maxX, pt.px)
    }
  }

  // Generate 3D Volumetric Voxels (Front, Back, Core)
  const group = new THREE.Group()
  const mat = new THREE.MeshStandardMaterial({
    color: colorHex,
    roughness: 0.35,
    metalness: 0.65,
  })

  const voxels: Array<{ x: number; y: number; z: number; scale: number }> = []

  for (const pt of activePoints) {
    const rowInfo = rowMap.get(pt.py) || { minX: pt.px, maxX: pt.px }
    const rowWidth = Math.max(1, rowInfo.maxX - rowInfo.minX)
    const rowCenter = (rowInfo.minX + rowInfo.maxX) / 2
    const rowRadius = rowWidth / 2

    // Normalized X in [-1, 1] relative to row center
    const u = Math.max(-1, Math.min(1, (pt.px - rowCenter) / (rowRadius || 1)))
    // Elliptical radial thickness factor
    const radiusFactor = Math.sqrt(Math.max(0, 1 - u * u))
    const zMax = (rowRadius / width) * 2.5 * radiusFactor * depthScale

    const nx = ((pt.px - minPx) / width - 0.5) * 2
    const ny = (0.5 - (pt.py - minPy) / height) * 2

    const posX = nx * aspect * 1.5
    const posY = ny * 1.5

    // 1. Front Surface Shell Voxel (+Z)
    voxels.push({ x: posX, y: posY, z: zMax, scale: 1.0 })
    // 2. Back Surface Shell Voxel (-Z)
    voxels.push({ x: posX, y: posY, z: -zMax, scale: 1.0 })

    // 3. Volumetric Core Fill Voxels along Z axis (full 360° volume)
    if (zMax > 0.08) {
      const steps = Math.min(4, Math.floor(zMax / 0.06))
      for (let s = 1; s < steps; s++) {
        const frac = (s / steps) * 2 - 1 // in (-1, 1)
        voxels.push({ x: posX, y: posY, z: zMax * frac, scale: 0.85 })
      }
    }
  }

  const cubeGeo = new THREE.BoxGeometry(0.065, 0.065, 0.065)
  const instancedMesh = new THREE.InstancedMesh(cubeGeo, mat, voxels.length)

  const dummy = new THREE.Object3D()
  for (let i = 0; i < voxels.length; i++) {
    const v = voxels[i]
    dummy.position.set(v.x, v.y, v.z)
    dummy.scale.setScalar(v.scale)
    dummy.updateMatrix()
    instancedMesh.setMatrixAt(i, dummy.matrix)
  }

  instancedMesh.instanceMatrix.needsUpdate = true
  group.add(instancedMesh)
  scene.add(group)
  return scene
}

export function exportSceneToGLBDataUrl(scene: THREE.Scene): Promise<string> {
  return new Promise((resolve, reject) => {
    const exporter = new GLTFExporter()
    exporter.parse(
      scene,
      (gltf) => {
        if (gltf instanceof ArrayBuffer) {
          const blob = new Blob([gltf], { type: "model/gltf-binary" })
          resolve(URL.createObjectURL(blob))
        } else {
          const json = JSON.stringify(gltf)
          const blob = new Blob([json], { type: "model/gltf+json" })
          resolve(URL.createObjectURL(blob))
        }
      },
      (err) => reject(err),
      { binary: true }
    )
  })
}
