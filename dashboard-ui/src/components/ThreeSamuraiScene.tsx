import { useEffect, useRef, useState } from "react"
import * as THREE from "three"

export type RenderStyle = "ascii" | "solid" | "wireframe"

interface ThreeSamuraiSceneProps {
  modelKey: "overview" | "bow" | "sword" | "brush" | "arts" | string
  color?: string
  renderStyle?: RenderStyle
  autoRotate?: boolean
}

export function ThreeSamuraiScene({
  modelKey,
  color = "#38bdf8",
  renderStyle = "ascii",
  autoRotate = true,
}: ThreeSamuraiSceneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const groupRef = useRef<THREE.Group | null>(null)
  const animIdRef = useRef<number | null>(null)
  const isDraggingRef = useRef(false)
  const prevMouseRef = useRef({ x: 0, y: 0 })
  const rotationVelocityRef = useRef({ x: 0, y: 0.005 })
  const [zoom, setZoom] = useState(1)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const width = container.clientWidth || 800
    const height = container.clientHeight || 600

    // 1. Scene & Camera
    const scene = new THREE.Scene()
    sceneRef.current = scene

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100)
    camera.position.set(0, 0, 7)

    // 2. Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.2
    rendererRef.current = renderer

    container.innerHTML = ""
    container.appendChild(renderer.domElement)

    // 3. Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.8)
    scene.add(ambientLight)

    const mainLight = new THREE.DirectionalLight(0xffffff, 2.0)
    mainLight.position.set(5, 8, 5)
    scene.add(mainLight)

    const rimLight = new THREE.DirectionalLight(new THREE.Color(color), 3.0)
    rimLight.position.set(-5, -3, -4)
    scene.add(rimLight)

    const pointLight = new THREE.PointLight(new THREE.Color(color), 2.5, 12)
    pointLight.position.set(0, 0, 2)
    scene.add(pointLight)

    // 4. Model Mesh Group
    const modelGroup = new THREE.Group()
    groupRef.current = modelGroup
    scene.add(modelGroup)

    buildModelGeometry(modelGroup, modelKey, color, renderStyle)

    // 5. Interaction Handlers
    function handlePointerDown(e: MouseEvent | TouchEvent) {
      isDraggingRef.current = true
      const clientX = "touches" in e ? e.touches[0].clientX : e.clientX
      const clientY = "touches" in e ? e.touches[0].clientY : e.clientY
      prevMouseRef.current = { x: clientX, y: clientY }
    }

    function handlePointerMove(e: MouseEvent | TouchEvent) {
      if (!isDraggingRef.current || !modelGroup) return
      const clientX = "touches" in e ? e.touches[0].clientX : e.clientX
      const clientY = "touches" in e ? e.touches[0].clientY : e.clientY
      const deltaX = clientX - prevMouseRef.current.x
      const deltaY = clientY - prevMouseRef.current.y
      prevMouseRef.current = { x: clientX, y: clientY }

      modelGroup.rotation.y += deltaX * 0.008
      modelGroup.rotation.x += deltaY * 0.008
      rotationVelocityRef.current = { x: deltaY * 0.002, y: deltaX * 0.002 }
    }

    function handlePointerUp() {
      isDraggingRef.current = false
    }

    function handleWheel(e: WheelEvent) {
      e.preventDefault()
      camera.position.z = THREE.MathUtils.clamp(camera.position.z + e.deltaY * 0.005, 3.5, 14)
      setZoom(Number((7 / camera.position.z).toFixed(2)))
    }

    const dom = renderer.domElement
    dom.addEventListener("mousedown", handlePointerDown)
    window.addEventListener("mousemove", handlePointerMove)
    window.addEventListener("mouseup", handlePointerUp)
    dom.addEventListener("touchstart", handlePointerDown, { passive: true })
    window.addEventListener("touchmove", handlePointerMove, { passive: true })
    window.addEventListener("touchend", handlePointerUp)
    dom.addEventListener("wheel", handleWheel, { passive: false })

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width: w, height: h } = entry.contentRect
        if (w > 0 && h > 0) {
          camera.aspect = w / h
          camera.updateProjectionMatrix()
          renderer.setSize(w, h)
        }
      }
    })
    ro.observe(container)

    // Animation Loop
    const clock = new THREE.Clock()
    function animate() {
      animIdRef.current = requestAnimationFrame(animate)
      const delta = clock.getDelta()

      if (modelGroup) {
        if (!isDraggingRef.current && autoRotate) {
          modelGroup.rotation.y += delta * 0.5
        }
        // Apply inertia dampening
        if (!isDraggingRef.current) {
          modelGroup.rotation.y += rotationVelocityRef.current.y
          modelGroup.rotation.x += rotationVelocityRef.current.x
          rotationVelocityRef.current.x *= 0.95
          rotationVelocityRef.current.y *= 0.95
        }
      }

      renderer.render(scene, camera)
    }
    animate()

    return () => {
      if (animIdRef.current) cancelAnimationFrame(animIdRef.current)
      ro.disconnect()
      dom.removeEventListener("mousedown", handlePointerDown)
      window.removeEventListener("mousemove", handlePointerMove)
      window.removeEventListener("mouseup", handlePointerUp)
      dom.removeEventListener("touchstart", handlePointerDown)
      window.removeEventListener("touchmove", handlePointerMove)
      window.removeEventListener("touchend", handlePointerUp)
      dom.removeEventListener("wheel", handleWheel)
      renderer.dispose()
    }
  }, [modelKey, color, renderStyle, autoRotate])

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", minHeight: 480 }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%", cursor: "grab" }} />
      {/* 3D Coordinate / Zoom HUD */}
      <div style={{
        position: "absolute",
        bottom: 12,
        right: 16,
        background: "rgba(15,23,42,0.75)",
        backdropFilter: "blur(8px)",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: 8,
        padding: "4px 10px",
        fontSize: "0.75rem",
        color: "#94a3b8",
        fontFamily: "monospace",
        pointerEvents: "none",
        display: "flex",
        gap: 12
      }}>
        <span>3D VIEW: {modelKey.toUpperCase()}</span>
        <span>MODE: {renderStyle.toUpperCase()}</span>
        <span>ZOOM: {zoom}x</span>
        <span>ORBIT: DRAG ↻</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3D Procedural Mesh Builder for Samurai Archetypes
// ---------------------------------------------------------------------------
function buildModelGeometry(
  parent: THREE.Group,
  key: string,
  themeColor: string,
  style: RenderStyle
) {
  const primaryColor = new THREE.Color(themeColor)
  const steelColor = new THREE.Color("#cbd5e1")
  const goldColor = new THREE.Color("#fbbf24")
  const darkColor = new THREE.Color("#1e293b")

  function createMaterial(baseColor: THREE.Color, roughness = 0.3, metalness = 0.8) {
    if (style === "wireframe") {
      return new THREE.MeshStandardMaterial({
        color: baseColor,
        wireframe: true,
        emissive: baseColor,
        emissiveIntensity: 0.4,
      })
    }
    if (style === "ascii") {
      // Holographic ASCII styling with custom wireframe & glowing vertex points
      return new THREE.MeshStandardMaterial({
        color: baseColor,
        wireframe: true,
        roughness: 0.1,
        metalness: 0.9,
        emissive: baseColor,
        emissiveIntensity: 0.7,
      })
    }
    // Solid PBR
    return new THREE.MeshStandardMaterial({
      color: baseColor,
      roughness,
      metalness,
      envMapIntensity: 1.2,
    })
  }

  // -------------------------------------------------------------------------
  // 1. KABUTO HELMET / SAMURAI OVERVIEW (3D Kabuto with Kuwagata Horns & Shikoro)
  // -------------------------------------------------------------------------
  if (key === "overview") {
    // Helmet Bowl (Hachi)
    const bowlGeo = new THREE.SphereGeometry(1.2, 32, 24, 0, Math.PI * 2, 0, Math.PI * 0.55)
    const bowlMat = createMaterial(darkColor, 0.4, 0.85)
    const bowl = new THREE.Mesh(bowlGeo, bowlMat)
    parent.add(bowl)

    // Gold Rim
    const rimGeo = new THREE.TorusGeometry(1.2, 0.06, 16, 48)
    rimGeo.rotateX(Math.PI / 2)
    const goldMat = createMaterial(goldColor, 0.2, 0.95)
    parent.add(new THREE.Mesh(rimGeo, goldMat))

    // Forehead Plate (Maedate Base) & Crest
    const crestPlateGeo = new THREE.CylinderGeometry(0.25, 0.25, 0.1, 16)
    crestPlateGeo.rotateX(Math.PI / 2)
    const crestPlate = new THREE.Mesh(crestPlateGeo, goldMat)
    crestPlate.position.set(0, 0.3, 1.2)
    parent.add(crestPlate)

    // Kuwagata Golden Horns (Swept V-Crest)
    const hornCurveL = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0.3, 1.2),
      new THREE.Vector3(-0.6, 1.1, 1.0),
      new THREE.Vector3(-1.4, 1.9, 0.6),
      new THREE.Vector3(-1.7, 2.2, 0.2),
    ])
    const hornGeoL = new THREE.TubeGeometry(hornCurveL, 24, 0.07, 8, false)
    parent.add(new THREE.Mesh(hornGeoL, goldMat))

    const hornCurveR = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0.3, 1.2),
      new THREE.Vector3(0.6, 1.1, 1.0),
      new THREE.Vector3(1.4, 1.9, 0.6),
      new THREE.Vector3(1.7, 2.2, 0.2),
    ])
    const hornGeoR = new THREE.TubeGeometry(hornCurveR, 24, 0.07, 8, false)
    parent.add(new THREE.Mesh(hornGeoR, goldMat))

    // Shikoro Neck Guard Tiers (Curved Back Plates)
    for (let t = 1; t <= 4; t++) {
      const tierRadius = 1.2 + t * 0.15
      const tierGeo = new THREE.CylinderGeometry(
        tierRadius,
        tierRadius + 0.1,
        0.18,
        32,
        1,
        true,
        Math.PI * 0.6,
        Math.PI * 1.8
      )
      const tierMat = createMaterial(t % 2 === 0 ? primaryColor : darkColor, 0.5, 0.7)
      const tier = new THREE.Mesh(tierGeo, tierMat)
      tier.position.y = -t * 0.22
      parent.add(tier)
    }

    // Menpo Face Guard (Mask)
    const maskGeo = new THREE.ConeGeometry(0.7, 0.9, 16, 1, true)
    maskGeo.rotateX(Math.PI)
    const maskMat = createMaterial(primaryColor, 0.3, 0.9)
    const mask = new THREE.Mesh(maskGeo, maskMat)
    mask.position.set(0, -0.4, 0.6)
    parent.add(mask)

    // Center Sun Disc (Hinomaru Crest)
    const sunDiscGeo = new THREE.RingGeometry(0, 0.35, 32)
    const sunMat = createMaterial(primaryColor, 0.2, 0.9)
    const sun = new THREE.Mesh(sunDiscGeo, sunMat)
    sun.position.set(0, 0.8, 1.25)
    parent.add(sun)
  }

  // -------------------------------------------------------------------------
  // 2. KATANA SWORD (True Curved Blade, Habaki, Tsuba, Tsuka, & Saya)
  // -------------------------------------------------------------------------
  else if (key === "sword") {
    // Curved Katana Blade (Sori Curve)
    const bladePoints = []
    const bladeLength = 4.2
    const soriDepth = 0.35 // Traditional curvature
    for (let i = 0; i <= 30; i++) {
      const t = i / 30
      const y = (t - 0.2) * bladeLength
      const x = Math.sin(t * Math.PI * 0.85) * soriDepth
      bladePoints.push(new THREE.Vector3(x, y, 0))
    }
    const bladeCurve = new THREE.CatmullRomCurve3(bladePoints)
    const bladeGeo = new THREE.TubeGeometry(bladeCurve, 64, 0.08, 6, false)
    const bladeMat = createMaterial(steelColor, 0.1, 0.98)
    const blade = new THREE.Mesh(bladeGeo, bladeMat)
    parent.add(blade)

    // Sharp Edge Glow Ridge
    const hamonCurve = new THREE.CatmullRomCurve3(
      bladePoints.map((p) => new THREE.Vector3(p.x + 0.04, p.y, p.z))
    )
    const hamonGeo = new THREE.TubeGeometry(hamonCurve, 64, 0.02, 4, false)
    const hamonMat = createMaterial(primaryColor, 0.2, 0.9)
    parent.add(new THREE.Mesh(hamonGeo, hamonMat))

    // Habaki (Blade Collar)
    const habakiGeo = new THREE.CylinderGeometry(0.14, 0.14, 0.25, 16)
    const habakiMat = createMaterial(goldColor, 0.2, 0.95)
    const habaki = new THREE.Mesh(habakiGeo, habakiMat)
    habaki.position.set(-0.02, -0.75, 0)
    parent.add(habaki)

    // Tsuba (Ornamental Sword Guard)
    const tsubaGeo = new THREE.CylinderGeometry(0.65, 0.65, 0.08, 32)
    const tsubaMat = createMaterial(goldColor, 0.3, 0.9)
    const tsuba = new THREE.Mesh(tsubaGeo, tsubaMat)
    tsuba.position.set(-0.04, -0.9, 0)
    parent.add(tsuba)

    // Tsuka (Handle / Hilt)
    const tsukaGeo = new THREE.CylinderGeometry(0.11, 0.12, 1.4, 16)
    const tsukaMat = createMaterial(darkColor, 0.7, 0.4)
    const tsuka = new THREE.Mesh(tsukaGeo, tsukaMat)
    tsuka.position.set(-0.05, -1.65, 0)
    parent.add(tsuka)

    // Handle Silk Ito Wrap Rings
    for (let r = 0; r < 8; r++) {
      const ringGeo = new THREE.TorusGeometry(0.12, 0.02, 8, 24)
      ringGeo.rotateX(Math.PI / 2)
      const ringMat = createMaterial(primaryColor, 0.5, 0.5)
      const ring = new THREE.Mesh(ringGeo, ringMat)
      ring.position.set(-0.05, -1.1 - r * 0.14, 0)
      parent.add(ring)
    }

    // Kashira (Pommel Cap)
    const kashiraGeo = new THREE.SphereGeometry(0.13, 16, 16, 0, Math.PI * 2, 0, Math.PI * 0.6)
    kashiraGeo.rotateX(Math.PI)
    const kashira = new THREE.Mesh(kashiraGeo, habakiMat)
    kashira.position.set(-0.05, -2.35, 0)
    parent.add(kashira)
  }

  // -------------------------------------------------------------------------
  // 3. YUMI BOW & ARROW (Asymmetric Japanese Longbow, String, & Ya Arrows)
  // -------------------------------------------------------------------------
  else if (key === "bow") {
    // Asymmetric Yumi Bow Stave (Traditional 2:1 upper:lower ratio)
    const bowPoints = []
    const numPts = 40
    for (let i = 0; i <= numPts; i++) {
      const t = i / numPts
      const y = (t - 0.35) * 5.2 // Asymmetric grip point at lower 1/3
      // Recurve curve shape
      const curveFactor = Math.sin(t * Math.PI) * 0.95 - (t > 0.85 ? (t - 0.85) * 1.8 : 0)
      const x = -curveFactor
      bowPoints.push(new THREE.Vector3(x, y, 0))
    }
    const bowCurve = new THREE.CatmullRomCurve3(bowPoints)
    const bowGeo = new THREE.TubeGeometry(bowCurve, 64, 0.09, 8, false)
    const bowMat = createMaterial(primaryColor, 0.4, 0.8)
    parent.add(new THREE.Mesh(bowGeo, bowMat))

    // Bowstring (Tsuru)
    const stringCurve = new THREE.LineCurve3(bowPoints[0], bowPoints[bowPoints.length - 1])
    const stringGeo = new THREE.TubeGeometry(stringCurve, 16, 0.015, 6, false)
    const stringMat = createMaterial(steelColor, 0.1, 0.5)
    parent.add(new THREE.Mesh(stringGeo, stringMat))

    // Ya (Feathered Samurai Arrow nocked across grip)
    const arrowShaftCurve = new THREE.LineCurve3(
      new THREE.Vector3(-1.8, 0, 0),
      new THREE.Vector3(2.6, 0, 0)
    )
    const arrowShaftGeo = new THREE.TubeGeometry(arrowShaftCurve, 16, 0.03, 6, false)
    const shaftMat = createMaterial(goldColor, 0.3, 0.7)
    parent.add(new THREE.Mesh(arrowShaftGeo, shaftMat))

    // Arrow Head (Yanone Metal Tip)
    const tipGeo = new THREE.ConeGeometry(0.09, 0.4, 8)
    tipGeo.rotateZ(-Math.PI / 2)
    const tipMat = createMaterial(steelColor, 0.1, 0.95)
    const tip = new THREE.Mesh(tipGeo, tipMat)
    tip.position.set(2.7, 0, 0)
    parent.add(tip)

    // Fletching (3 Feathers on Arrow End)
    for (let f = 0; f < 3; f++) {
      const fletchGeo = new THREE.BoxGeometry(0.5, 0.12, 0.01)
      fletchGeo.rotateX((f * Math.PI * 2) / 3)
      const fletchMat = createMaterial(primaryColor, 0.6, 0.3)
      const feather = new THREE.Mesh(fletchGeo, fletchMat)
      feather.position.set(-1.4, 0, 0)
      parent.add(feather)
    }

    // Grip Wrapping (Nigiri)
    const gripGeo = new THREE.CylinderGeometry(0.11, 0.11, 0.5, 16)
    const gripMat = createMaterial(darkColor, 0.8, 0.3)
    const grip = new THREE.Mesh(gripGeo, gripMat)
    grip.position.set(bowPoints[14].x, bowPoints[14].y, 0)
    parent.add(grip)
  }

  // -------------------------------------------------------------------------
  // 4. FUDE CALLIGRAPHY BRUSH & INKWELL (Bamboo Handle, Ferrule, Hair Tip)
  // -------------------------------------------------------------------------
  else if (key === "brush") {
    // Tapered Bamboo Shaft with Node Joints
    const shaftGeo = new THREE.CylinderGeometry(0.09, 0.13, 3.8, 20)
    const shaftMat = createMaterial(goldColor, 0.4, 0.6)
    const shaft = new THREE.Mesh(shaftGeo, shaftMat)
    shaft.position.y = 0.8
    parent.add(shaft)

    // Bamboo Joint Rings
    for (let j = 0; j < 4; j++) {
      const ringGeo = new THREE.TorusGeometry(0.125, 0.025, 12, 24)
      ringGeo.rotateX(Math.PI / 2)
      const ringMat = createMaterial(darkColor, 0.6, 0.4)
      const ring = new THREE.Mesh(ringGeo, ringMat)
      ring.position.y = -0.4 + j * 0.85
      parent.add(ring)
    }

    // Metallic Ferrule Collar
    const ferruleGeo = new THREE.CylinderGeometry(0.14, 0.18, 0.4, 20)
    const ferruleMat = createMaterial(steelColor, 0.2, 0.95)
    const ferrule = new THREE.Mesh(ferruleGeo, ferruleMat)
    ferrule.position.y = -1.15
    parent.add(ferrule)

    // Sculpted Goat-Hair Brush Tip (Tapered Tear-Drop Teardrop)
    const tipPoints = [
      new THREE.Vector2(0, -2.4),
      new THREE.Vector2(0.08, -2.2),
      new THREE.Vector2(0.18, -1.9),
      new THREE.Vector2(0.21, -1.6),
      new THREE.Vector2(0.17, -1.35),
      new THREE.Vector2(0, -1.35),
    ]
    const tipGeo = new THREE.LatheGeometry(tipPoints, 24)
    const tipMat = createMaterial(primaryColor, 0.8, 0.2)
    const brushTip = new THREE.Mesh(tipGeo, tipMat)
    parent.add(brushTip)

    // Hanging Thread Loop at Top
    const loopGeo = new THREE.TorusGeometry(0.08, 0.015, 8, 20)
    const loopMat = createMaterial(primaryColor, 0.5, 0.5)
    const loop = new THREE.Mesh(loopGeo, loopMat)
    loop.position.y = 2.75
    parent.add(loop)
  }

  // -------------------------------------------------------------------------
  // 5. PAGODA CASTLE (Arts - Multi-Tier Japanese Pagoda Fortress & Roof Eaves)
  // -------------------------------------------------------------------------
  else if (key === "arts") {
    // Stone Base (Tenshu Foundation)
    const baseGeo = new THREE.CylinderGeometry(1.6, 2.0, 0.6, 4)
    baseGeo.rotateY(Math.PI / 4)
    const baseMat = createMaterial(darkColor, 0.9, 0.2)
    const base = new THREE.Mesh(baseGeo, baseMat)
    base.position.y = -1.8
    parent.add(base)

    // 4 Pagoda Tiers with Curved Eaves
    const numTiers = 4
    for (let t = 0; t < numTiers; t++) {
      const tierY = -1.2 + t * 0.95
      const scale = 1.0 - t * 0.18

      // Room Body / Walls
      const wallGeo = new THREE.BoxGeometry(1.4 * scale, 0.65, 1.4 * scale)
      const wallMat = createMaterial(primaryColor, 0.4, 0.7)
      const wall = new THREE.Mesh(wallGeo, wallMat)
      wall.position.y = tierY
      parent.add(wall)

      // Swept Roof Eaves (Curved Japanese Hip-and-Gable roof)
      const roofGeo = new THREE.ConeGeometry(2.1 * scale, 0.35, 4)
      roofGeo.rotateY(Math.PI / 4)
      const roofMat = createMaterial(t === numTiers - 1 ? goldColor : darkColor, 0.3, 0.9)
      const roof = new THREE.Mesh(roofGeo, roofMat)
      roof.position.y = tierY + 0.45
      parent.add(roof)

      // Corner Bell Finials
      for (let c = 0; c < 4; c++) {
        const angle = (c * Math.PI) / 2 + Math.PI / 4
        const bellGeo = new THREE.SphereGeometry(0.06, 8, 8)
        const bell = new THREE.Mesh(bellGeo, roofMat)
        bell.position.set(
          Math.cos(angle) * 1.5 * scale,
          tierY + 0.3,
          Math.sin(angle) * 1.5 * scale
        )
        parent.add(bell)
      }
    }

    // Sorin Spire (Top Temple Finial)
    const spireGeo = new THREE.CylinderGeometry(0.04, 0.08, 1.2, 16)
    const spireMat = createMaterial(goldColor, 0.2, 0.95)
    const spire = new THREE.Mesh(spireGeo, spireMat)
    spire.position.y = 2.4
    parent.add(spire)

    // Sacred Rings on Spire
    for (let r = 0; r < 5; r++) {
      const ringGeo = new THREE.TorusGeometry(0.12 - r * 0.015, 0.02, 8, 20)
      ringGeo.rotateX(Math.PI / 2)
      const ring = new THREE.Mesh(ringGeo, spireMat)
      ring.position.y = 2.0 + r * 0.15
      parent.add(ring)
    }
  }

  // Initial tilt for dynamic presentation
  parent.rotation.x = 0.15
  parent.rotation.y = -0.35
}
