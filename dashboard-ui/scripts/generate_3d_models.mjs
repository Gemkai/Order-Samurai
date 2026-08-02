import * as THREE from "three"
import { GLTFExporter } from "three/examples/jsm/exporters/GLTFExporter.js"
import fs from "node:fs"
import path from "node:path"

const modelsDir = path.resolve("public/models")
if (!fs.existsSync(modelsDir)) {
  fs.mkdirSync(modelsDir, { recursive: true })
}

function exportSceneToGLTF(scene, filename) {
  const exporter = new GLTFExporter()
  exporter.parse(
    scene,
    (gltf) => {
      const json = typeof gltf === "string" ? gltf : JSON.stringify(gltf, null, 2)
      fs.writeFileSync(path.join(modelsDir, filename), json)
      console.log(`Exported ${filename} (${json.length} bytes)`)
    },
    (err) => console.error(`Error exporting ${filename}:`, err),
    { binary: false }
  )
}

// 1. OVERVIEW / SAMURAI HELMET 3D MODEL
function createSamuraiScene() {
  const scene = new THREE.Scene()
  const mat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.3, metalness: 0.8 })
  const goldMat = new THREE.MeshStandardMaterial({ color: 0xfacc15, roughness: 0.2, metalness: 0.9 })
  const darkMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.5, metalness: 0.5 })

  // Helmet Bowl (Hachi)
  const hachiGeo = new THREE.SphereGeometry(1.2, 32, 16, 0, Math.PI * 2, 0, Math.PI * 0.55)
  const hachi = new THREE.Mesh(hachiGeo, mat)
  hachi.position.y = 0.2
  scene.add(hachi)

  // Helmet Rim (Peak / Maezashi)
  const rimGeo = new THREE.TorusGeometry(1.22, 0.1, 16, 32, Math.PI)
  const rim = new THREE.Mesh(rimGeo, goldMat)
  rim.rotation.x = Math.PI * 0.4
  rim.position.set(0, 0.2, 0.2)
  scene.add(rim)

  // Kuwagata Horns (Crest)
  const hornGroup = new THREE.Group()
  for (const side of [-1, 1]) {
    const hornGeo = new THREE.ConeGeometry(0.18, 1.8, 16)
    const horn = new THREE.Mesh(hornGeo, goldMat)
    horn.position.set(side * 0.6, 1.4, 0.3)
    horn.rotation.z = side * -0.55
    horn.rotation.x = -0.2
    hornGroup.add(horn)
  }
  scene.add(hornGroup)

  // Crest Emblem (Maedate)
  const emblemGeo = new THREE.CylinderGeometry(0.35, 0.35, 0.08, 32)
  const emblem = new THREE.Mesh(emblemGeo, goldMat)
  emblem.rotation.x = Math.PI / 2
  emblem.position.set(0, 1.1, 1.1)
  scene.add(emblem)

  // Menpo Mask (Face Guard)
  const maskGeo = new THREE.CylinderGeometry(0.9, 0.7, 0.9, 32, 1, true, -Math.PI * 0.35, Math.PI * 0.7)
  const mask = new THREE.Mesh(maskGeo, darkMat)
  mask.position.set(0, -0.3, 0.3)
  scene.add(mask)

  // Neck Guard Plates (Shikoro)
  for (let i = 0; i < 3; i++) {
    const plateGeo = new THREE.TorusGeometry(1.3 + i * 0.12, 0.06, 8, 32, Math.PI * 1.1)
    const plate = new THREE.Mesh(plateGeo, mat)
    plate.rotation.x = Math.PI * 0.55
    plate.position.set(0, -0.1 - i * 0.25, -0.1)
    scene.add(plate)
  }

  return scene
}

// 2. SWORD / KATANA 3D MODEL
function createSwordScene() {
  const scene = new THREE.Scene()
  const steelMat = new THREE.MeshStandardMaterial({ color: 0xf1f5f9, roughness: 0.1, metalness: 0.95 })
  const redMat = new THREE.MeshStandardMaterial({ color: 0xef4444, roughness: 0.3, metalness: 0.7 })
  const goldMat = new THREE.MeshStandardMaterial({ color: 0xfacc15, roughness: 0.2, metalness: 0.9 })

  // Katana Blade (Curved)
  const bladeGeo = new THREE.BoxGeometry(0.12, 3.8, 0.3)
  const blade = new THREE.Mesh(bladeGeo, steelMat)
  blade.position.y = 1.2
  blade.rotation.z = -0.1
  scene.add(blade)

  // Tsuba Guard
  const tsubaGeo = new THREE.CylinderGeometry(0.55, 0.55, 0.08, 32)
  const tsuba = new THREE.Mesh(tsubaGeo, goldMat)
  tsuba.position.y = -0.7
  scene.add(tsuba)

  // Tsuka Handle
  const handleGeo = new THREE.CylinderGeometry(0.18, 0.18, 1.4, 16)
  const handle = new THREE.Mesh(handleGeo, redMat)
  handle.position.y = -1.4
  scene.add(handle)

  // Kashira Pommel
  const pommelGeo = new THREE.SphereGeometry(0.22, 16, 16)
  const pommel = new THREE.Mesh(pommelGeo, goldMat)
  pommel.position.y = -2.1
  scene.add(pommel)

  return scene
}

// 3. BOW / ARCHERY YUMI 3D MODEL
function createBowScene() {
  const scene = new THREE.Scene()
  const amberMat = new THREE.MeshStandardMaterial({ color: 0xf59e0b, roughness: 0.4, metalness: 0.6 })
  const woodMat = new THREE.MeshStandardMaterial({ color: 0x78350f, roughness: 0.7 })
  const stringMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.1 })

  // Bow Curve
  const bowCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 2.2, 0),
    new THREE.Vector3(0.7, 1.2, 0),
    new THREE.Vector3(0.5, 0, 0),
    new THREE.Vector3(0.8, -1.2, 0),
    new THREE.Vector3(0, -2.2, 0),
  ])
  const tubeGeo = new THREE.TubeGeometry(bowCurve, 64, 0.1, 16, false)
  const bow = new THREE.Mesh(tubeGeo, amberMat)
  scene.add(bow)

  // Bowstring
  const stringGeo = new THREE.CylinderGeometry(0.02, 0.02, 4.4, 8)
  const string = new THREE.Mesh(stringGeo, stringMat)
  string.position.x = 0
  scene.add(string)

  // Arrow Shaft
  const arrowGeo = new THREE.CylinderGeometry(0.04, 0.04, 3.2, 16)
  const arrow = new THREE.Mesh(arrowGeo, woodMat)
  arrow.rotation.z = Math.PI / 2
  arrow.position.set(0.6, 0, 0)
  scene.add(arrow)

  // Arrowhead
  const headGeo = new THREE.ConeGeometry(0.12, 0.4, 16)
  const head = new THREE.Mesh(headGeo, amberMat)
  head.rotation.z = -Math.PI / 2
  head.position.set(2.2, 0, 0)
  scene.add(head)

  return scene
}

// 4. BRUSH / INK FUDE 3D MODEL
function createBrushScene() {
  const scene = new THREE.Scene()
  const pinkMat = new THREE.MeshStandardMaterial({ color: 0xec4899, roughness: 0.3, metalness: 0.5 })
  const bambooMat = new THREE.MeshStandardMaterial({ color: 0xfbcfe8, roughness: 0.6 })

  // Handle
  const handleGeo = new THREE.CylinderGeometry(0.16, 0.16, 3.6, 24)
  const handle = new THREE.Mesh(handleGeo, bambooMat)
  handle.position.y = 0.5
  scene.add(handle)

  // Joints
  for (let y of [-0.5, 0.3, 1.1, 1.9]) {
    const ringGeo = new THREE.TorusGeometry(0.17, 0.03, 12, 24)
    const ring = new THREE.Mesh(ringGeo, pinkMat)
    ring.rotation.x = Math.PI / 2
    ring.position.y = y
    scene.add(ring)
  }

  // Brush Tip
  const tipGeo = new THREE.ConeGeometry(0.4, 1.2, 32)
  const tip = new THREE.Mesh(tipGeo, pinkMat)
  tip.rotation.x = Math.PI
  tip.position.y = -1.9
  scene.add(tip)

  return scene
}

// 5. ARTS / PAGODA CASTLE 3D MODEL
function createArtsScene() {
  const scene = new THREE.Scene()
  const whiteMat = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.2 })
  const roofMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.4, metalness: 0.7 })
  const stoneMat = new THREE.MeshStandardMaterial({ color: 0x475569, roughness: 0.8 })

  // Stone Base
  const baseGeo = new THREE.CylinderGeometry(2.0, 2.6, 1.2, 4)
  const base = new THREE.Mesh(baseGeo, stoneMat)
  base.position.y = -1.2
  base.rotation.y = Math.PI / 4
  scene.add(base)

  // Tier 1 Roof
  const roof1Geo = new THREE.ConeGeometry(2.8, 0.8, 4)
  const roof1 = new THREE.Mesh(roof1Geo, roofMat)
  roof1.position.y = -0.2
  roof1.rotation.y = Math.PI / 4
  scene.add(roof1)

  // Tier 2 Walls
  const wall2Geo = new THREE.BoxGeometry(1.8, 0.9, 1.8)
  const wall2 = new THREE.Mesh(wall2Geo, whiteMat)
  wall2.position.y = 0.5
  scene.add(wall2)

  // Tier 2 Roof
  const roof2Geo = new THREE.ConeGeometry(2.2, 0.8, 4)
  const roof2 = new THREE.Mesh(roof2Geo, roofMat)
  roof2.position.y = 1.1
  roof2.rotation.y = Math.PI / 4
  scene.add(roof2)

  // Tier 3 Roof (Top)
  const roof3Geo = new THREE.ConeGeometry(1.5, 0.9, 4)
  const roof3 = new THREE.Mesh(roof3Geo, roofMat)
  roof3.position.y = 1.8
  roof3.rotation.y = Math.PI / 4
  scene.add(roof3)

  return scene
}

exportSceneToGLTF(createSamuraiScene(), "samurai.gltf")
exportSceneToGLTF(createSwordScene(), "sword.gltf")
exportSceneToGLTF(createBowScene(), "bow.gltf")
exportSceneToGLTF(createBrushScene(), "brush.gltf")
exportSceneToGLTF(createArtsScene(), "arts.gltf")
