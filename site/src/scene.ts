import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';

/*
 * The persistent "memory ledger" scene.
 * One Three.js world, morphed by a single scroll progress value (0..1) across four beats:
 *   0.00–0.25  HERO       — a slowly rotating hash-chained ledger drifting in space
 *   0.25–0.50  CORRECTION — the camera dives; a stale block is retired to a side-rail, a fresh one snaps in
 *   0.50–0.75  ATTACK     — a red "go back" command flies at the chain and shatters on an auth barrier
 *   0.75–1.00  ERASURE    — a block collapses to a sealed wireframe ghost while an auditor scan-line sweeps
 */

const SIGNAL = new THREE.Color('#38f2d6');
const SIGNAL_DIM = new THREE.Color('#0e5c55');
const RETIRED = new THREE.Color('#3a3a44');
const DANGER = new THREE.Color('#ff3b47');
const INK = new THREE.Color('#0a0a0b');

const BLOCK_COUNT = 8;
const GAP = 3.4;

const HASHES = [
  'a19f4c', '7e2b08', 'c4d9f1', 'db31ae', '5fa662', '90c7d4', '2e8b3f', 'bb01c9', 'e4f7a2',
];

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function hashTexture(text: string, tint = '#38f2d6'): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = 256;
  c.height = 256;
  const ctx = c.getContext('2d')!;
  ctx.fillStyle = 'rgba(10,10,12,0.9)';
  ctx.fillRect(0, 0, 256, 256);
  ctx.strokeStyle = 'rgba(56,242,214,0.14)';
  ctx.lineWidth = 2;
  ctx.strokeRect(10, 10, 236, 236);
  ctx.fillStyle = tint;
  ctx.font = '600 40px "JetBrains Mono", monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.globalAlpha = 0.85;
  ctx.fillText('0x' + text, 128, 118);
  ctx.globalAlpha = 0.4;
  ctx.font = '500 22px "JetBrains Mono", monospace';
  ctx.fillText('sha256 · chained', 128, 168);
  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = 4;
  return tex;
}

interface Block {
  group: THREE.Group;
  core: THREE.Mesh;
  edges: THREE.LineSegments;
  glow: THREE.Sprite;
  home: THREE.Vector3;
  baseColor: THREE.Color;
}

export interface SceneHandle {
  setProgress(p: number): void;
  setPointer(nx: number, ny: number): void;
  resize(): void;
  reducedMotion: boolean;
}

export function initScene(canvas: HTMLCanvasElement, opts: { reducedMotion: boolean; lowPower: boolean }): SceneHandle {
  const { reducedMotion, lowPower } = opts;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: !lowPower,
    alpha: true,
    powerPreference: 'high-performance',
  });
  renderer.setClearColor(INK, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, lowPower ? 1.5 : 2));

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(INK.getHex(), 0.028);

  const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 200);
  camera.position.set(0, 1.4, 11);

  // Lighting — cold key + signal rim.
  const key = new THREE.DirectionalLight(0xbfefff, 1.1);
  key.position.set(4, 8, 6);
  scene.add(key);
  const rim = new THREE.PointLight(SIGNAL.getHex(), 2.4, 40, 2);
  rim.position.set(-6, 2, 4);
  scene.add(rim);
  scene.add(new THREE.AmbientLight(0x223035, 0.6));

  // ----- The ledger -----
  const ledger = new THREE.Group();
  scene.add(ledger);

  const boxGeo = new THREE.BoxGeometry(1.7, 1.7, 1.7);
  const edgeGeo = new THREE.EdgesGeometry(boxGeo);

  const glowTex = (() => {
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const ctx = c.getContext('2d')!;
    const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, 'rgba(56,242,214,0.55)');
    g.addColorStop(0.4, 'rgba(56,242,214,0.16)');
    g.addColorStop(1, 'rgba(56,242,214,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  })();

  function makeBlock(i: number, hash: string, color: THREE.Color): Block {
    const group = new THREE.Group();

    const core = new THREE.Mesh(
      boxGeo,
      new THREE.MeshPhysicalMaterial({
        color: INK.clone(),
        metalness: 0.1,
        roughness: 0.25,
        transmission: lowPower ? 0 : 0.35,
        thickness: 1.2,
        ior: 1.3,
        transparent: true,
        opacity: lowPower ? 0.5 : 0.92,
        emissive: color.clone(),
        emissiveIntensity: 0.16,
        clearcoat: 0.6,
      }),
    );
    // Hash face texture on the front/back.
    const label = hashTexture(hash);
    const facePlane = new THREE.Mesh(
      new THREE.PlaneGeometry(1.45, 1.45),
      new THREE.MeshBasicMaterial({ map: label, transparent: true, opacity: 0.9 }),
    );
    facePlane.position.z = 0.87;
    core.add(facePlane);

    const edges = new THREE.LineSegments(
      edgeGeo,
      new THREE.LineBasicMaterial({ color: color.clone(), transparent: true, opacity: 0.9 }),
    );

    const glow = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: glowTex, color: color.clone(), transparent: true, blending: THREE.AdditiveBlending, opacity: 0.7, depthWrite: false }),
    );
    // 2026-10-26: 4.2 put a halo about 2.5x the block's own width over the headline, so the hero read
    // as a glow with words behind it. The block is the subject; the glow is light around it.
    glow.scale.setScalar(2.6);

    group.add(glow, core, edges);

    const x = Math.sin(i * 0.7) * 0.9;
    const y = Math.cos(i * 0.55) * 0.5;
    const z = -i * GAP;
    group.position.set(x, y, z);
    ledger.add(group);

    return { group, core, edges, glow, home: group.position.clone(), baseColor: color.clone() };
  }

  const blocks: Block[] = [];
  for (let i = 0; i < BLOCK_COUNT; i++) {
    blocks.push(makeBlock(i, HASHES[i], SIGNAL));
  }

  // Chain links between blocks (thin bright connectors).
  const linkMat = new THREE.LineBasicMaterial({ color: SIGNAL.clone(), transparent: true, opacity: 0.28 });
  for (let i = 0; i < BLOCK_COUNT - 1; i++) {
    const g = new THREE.BufferGeometry().setFromPoints([blocks[i].home, blocks[i + 1].home]);
    ledger.add(new THREE.Line(g, linkMat));
  }

  // The "eu-west" replacement block for the correction beat (hidden until then).
  const CORRECT_IDX = 3;
  const fresh = makeBlock(BLOCK_COUNT, 'eu-w01', SIGNAL);
  fresh.group.scale.setScalar(0.001);
  fresh.group.position.copy(blocks[CORRECT_IDX].home);

  // ----- Attack: red intruder + auth barrier -----
  const intruder = new THREE.Group();
  const intruderMesh = new THREE.Mesh(
    new THREE.TetrahedronGeometry(0.9, 0),
    new THREE.MeshStandardMaterial({ color: DANGER, emissive: DANGER, emissiveIntensity: 0.8, roughness: 0.4, transparent: true, opacity: 0.95 }),
  );
  intruder.add(intruderMesh);
  intruder.visible = false;
  scene.add(intruder);

  const barrier = new THREE.Mesh(
    new THREE.PlaneGeometry(7, 7, 1, 1),
    new THREE.MeshBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
  const barrierWire = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.PlaneGeometry(7, 7, 6, 6)),
    new THREE.LineBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0 }),
  );
  const barrierGroup = new THREE.Group();
  barrierGroup.add(barrier, barrierWire);
  barrierGroup.position.set(0, 0, blocks[CORRECT_IDX].home.z + 4.2);
  scene.add(barrierGroup);

  // Shatter particles for the intruder impact.
  const SHARDS = 120;
  const shardGeo = new THREE.BufferGeometry();
  const shardPos = new Float32Array(SHARDS * 3);
  const shardVel: THREE.Vector3[] = [];
  for (let i = 0; i < SHARDS; i++) {
    shardVel.push(new THREE.Vector3((Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2).normalize());
  }
  shardGeo.setAttribute('position', new THREE.BufferAttribute(shardPos, 3));
  const shards = new THREE.Points(shardGeo, new THREE.PointsMaterial({ color: DANGER, size: 0.14, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
  scene.add(shards);

  // ----- Erasure: tombstone seal + auditor scan-line -----
  const ERASE_IDX = 5;
  const seal = new THREE.Mesh(
    new THREE.TorusGeometry(1.6, 0.05, 12, 48),
    new THREE.MeshBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0, blending: THREE.AdditiveBlending }),
  );
  seal.position.copy(blocks[ERASE_IDX].home);
  scene.add(seal);

  const scanLine = new THREE.Mesh(
    new THREE.PlaneGeometry(14, 8),
    new THREE.MeshBasicMaterial({ color: SIGNAL, transparent: true, opacity: 0, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
  scanLine.rotation.y = Math.PI / 2;
  scanLine.visible = false;
  scene.add(scanLine);

  // ----- Ambient particle field (the vector cloud) -----
  const PCOUNT = lowPower ? 500 : 1400;
  const pGeo = new THREE.BufferGeometry();
  const pPos = new Float32Array(PCOUNT * 3);
  for (let i = 0; i < PCOUNT; i++) {
    pPos[i * 3] = (Math.random() - 0.5) * 34;
    pPos[i * 3 + 1] = (Math.random() - 0.5) * 20;
    pPos[i * 3 + 2] = -Math.random() * (BLOCK_COUNT * GAP) - 6;
  }
  pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
  const particles = new THREE.Points(pGeo, new THREE.PointsMaterial({ color: SIGNAL, size: 0.05, transparent: true, opacity: 0.5, depthWrite: false }));
  scene.add(particles);

  // ----- Post-processing (desktop only) -----
  let composer: EffectComposer | null = null;
  if (!lowPower) {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    // Strength 0.62 spread the ledger's light across the text column; 0.4 keeps the edges lit and
    // stops the spill (2026-10-26).
    const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.4, 0.5, 0.2);
    composer.addPass(bloom);
  }

  // ----- State -----
  let progress = 0;
  let smoothProgress = 0;
  const pointer = new THREE.Vector2(0, 0);
  const pointerTarget = new THREE.Vector2(0, 0);
  const clock = new THREE.Clock();

  function resize() {
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    if (composer) composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();

  // Camera key positions per beat (world Z of the block we frame).
  // The hero beat frames the chain to the RIGHT of the headline by aiming the camera left of the
  // chain (HERO_LOOK_X), not by moving the camera sideways: a sideways camera swings the far blocks
  // further INTO the text column, which is how -4.2 and -8.0 both ended up lying across "fact"
  // (measured on the rendered page, 2026-10-26). The aim returns to x = 0 as the hero beat ends, so
  // the later beats keep their own framing.
  // HERO_LEDGER_X slides the whole chain to the right of the headline for the hero beat only, and
  // slides back to 0 as the beat ends, so every later beat keeps its own framing. Aiming the camera
  // sideways instead (HERO_LOOK_X, tried at -4.5 and -3.0) clears the text too, but it flattens the
  // chain towards the viewer and the hash-chain recession is the whole point of the shot.
  const HERO_LEDGER_X = 4.4;
  const camHero = new THREE.Vector3(0, 1.4, 11);
  const camCorrect = new THREE.Vector3(2.4, 0.8, blocks[CORRECT_IDX].home.z + 6.5);
  const camAttack = new THREE.Vector3(0, 0.4, blocks[CORRECT_IDX].home.z + 8.5);
  const camErase = new THREE.Vector3(3.0, 1.0, blocks[ERASE_IDX].home.z + 6.5);
  const lookTarget = new THREE.Vector3();

  const tmpColor = new THREE.Color();

  function update() {
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;

    smoothProgress += (progress - smoothProgress) * (reducedMotion ? 1 : 0.08);
    pointer.x += (pointerTarget.x - pointer.x) * 0.06;
    pointer.y += (pointerTarget.y - pointer.y) * 0.06;
    const p = smoothProgress;

    // --- Camera path ---
    let camPos: THREE.Vector3;
    let look = new THREE.Vector3(0, 0, blocks[0].home.z);
    if (p < 0.25) {
      const k = smoothstep(0, 0.25, p);
      camPos = camHero.clone().lerp(camCorrect, k);
      look.set(0, 0, lerp(-2, blocks[CORRECT_IDX].home.z, k));
      ledger.position.x = lerp(HERO_LEDGER_X, 0, k);
    } else if (p < 0.5) {
      const k = smoothstep(0.25, 0.5, p);
      camPos = camCorrect.clone().lerp(camAttack, k);
      look.set(0, 0, blocks[CORRECT_IDX].home.z);
    } else if (p < 0.75) {
      const k = smoothstep(0.5, 0.75, p);
      camPos = camAttack.clone().lerp(camErase, k);
      look.set(0, 0, lerp(blocks[CORRECT_IDX].home.z, blocks[ERASE_IDX].home.z, k));
    } else {
      camPos = camErase.clone();
      look.set(0, 0, blocks[ERASE_IDX].home.z);
    }
    // Pointer parallax.
    camPos.x += pointer.x * 1.1;
    camPos.y += pointer.y * 0.7;
    camera.position.lerp(camPos, reducedMotion ? 1 : 0.1);
    lookTarget.lerp(look, reducedMotion ? 1 : 0.1);
    camera.lookAt(lookTarget);

    // --- Hero idle rotation ---
    const heroFade = 1 - smoothstep(0.12, 0.28, p);
    if (!reducedMotion) {
      ledger.rotation.y = Math.sin(t * 0.12) * 0.14 * heroFade + 0.02;
      ledger.rotation.x = Math.cos(t * 0.1) * 0.05 * heroFade;
    }

    // --- Correction beat ---
    const corr = smoothstep(0.26, 0.46, p);
    const target = blocks[CORRECT_IDX];
    // Retire the stale block to a side-rail, grey it out.
    target.group.position.x = lerp(target.home.x, target.home.x + 4.6, corr);
    target.group.position.y = lerp(target.home.y, target.home.y + 1.4, corr);
    target.group.scale.setScalar(lerp(1, 0.72, corr));
    tmpColor.copy(target.baseColor).lerp(RETIRED, corr);
    (target.edges.material as THREE.LineBasicMaterial).color.copy(tmpColor);
    (target.edges.material as THREE.LineBasicMaterial).opacity = lerp(0.9, 0.35, corr);
    (target.glow.material as THREE.SpriteMaterial).opacity = lerp(0.7, 0.05, corr);
    // Fresh block snaps into the vacated slot.
    if (corr > 0.001 && fresh.group.parent !== ledger) ledger.add(fresh.group);
    const snap = smoothstep(0.32, 0.48, p);
    fresh.group.scale.setScalar(Math.max(0.001, snap));
    (fresh.glow.material as THREE.SpriteMaterial).opacity = snap * 0.8;

    // --- Attack beat ---
    intruder.visible = p > 0.5 && p < 0.76;
    const atkIn = smoothstep(0.5, 0.62, p); // approach
    const atkHit = smoothstep(0.62, 0.66, p); // impact
    const barrierZ = barrierGroup.position.z;
    const startX = 12;
    intruder.position.set(lerp(startX, 0, atkIn), lerp(3, 0, atkIn), lerp(blocks[CORRECT_IDX].home.z + 12, barrierZ + 0.6, atkIn));
    intruder.rotation.x += dt * 3;
    intruder.rotation.y += dt * 4;
    (intruderMesh.material as THREE.MeshStandardMaterial).opacity = 0.95 * (1 - atkHit);
    intruderMesh.scale.setScalar(lerp(1, 0.2, atkHit));
    // Barrier flashes on impact.
    const barrierGlow = smoothstep(0.5, 0.62, p) * (0.35 + 0.65 * Math.abs(Math.sin(t * 6))) * (1 - smoothstep(0.7, 0.76, p));
    (barrier.material as THREE.MeshBasicMaterial).opacity = barrierGlow * 0.18;
    (barrierWire.material as THREE.LineBasicMaterial).opacity = barrierGlow * 0.9;
    // Shatter particles.
    const shatter = smoothstep(0.63, 0.72, p);
    (shards.material as THREE.PointsMaterial).opacity = shatter * (1 - smoothstep(0.72, 0.78, p));
    const pa = shardGeo.getAttribute('position') as THREE.BufferAttribute;
    for (let i = 0; i < SHARDS; i++) {
      const r = shatter * 6;
      pa.setXYZ(i, shardVel[i].x * r, shardVel[i].y * r, barrierZ + shardVel[i].z * r);
    }
    pa.needsUpdate = true;

    // --- Erasure beat ---
    const er = smoothstep(0.78, 0.94, p);
    const eb = blocks[ERASE_IDX];
    const em = eb.core.material as THREE.MeshPhysicalMaterial;
    em.wireframe = er > 0.5;
    em.opacity = lerp(0.92, 0.16, er);
    em.emissiveIntensity = lerp(0.16, 0.02, er);
    (eb.edges.material as THREE.LineBasicMaterial).opacity = lerp(0.9, 0.25, er);
    tmpColor.copy(eb.baseColor).lerp(new THREE.Color('#8892a0'), er);
    (eb.edges.material as THREE.LineBasicMaterial).color.copy(tmpColor);
    (eb.glow.material as THREE.SpriteMaterial).opacity = lerp(0.7, 0.1, er);
    eb.group.scale.setScalar(lerp(1, 0.86, er));
    // Seal ring.
    (seal.material as THREE.MeshBasicMaterial).opacity = er * 0.9;
    seal.rotation.z += dt * 0.8;
    seal.rotation.x = Math.PI / 2 + Math.sin(t) * 0.15;
    seal.scale.setScalar(lerp(0.3, 1.15, er));
    // Auditor scan-line sweeps across the cloud around the erased block.
    scanLine.visible = er > 0.01 && er < 0.99;
    (scanLine.material as THREE.MeshBasicMaterial).opacity = er * (1 - er) * 1.6 * 0.5;
    const sweep = (t * 3.2) % (BLOCK_COUNT * GAP);
    scanLine.position.z = eb.home.z + 6 - sweep;

    // --- Particles drift ---
    if (!reducedMotion) {
      particles.rotation.y = t * 0.01;
      const arr = pGeo.getAttribute('position') as THREE.BufferAttribute;
      for (let i = 0; i < PCOUNT; i++) {
        let y = arr.getY(i) + dt * 0.15;
        if (y > 10) y = -10;
        arr.setY(i, y);
      }
      arr.needsUpdate = true;
    }
    rim.intensity = 2.0 + Math.sin(t * 2) * 0.4;

    if (composer) composer.render();
    else renderer.render(scene, camera);
  }

  let raf = 0;
  function loop() {
    update();
    raf = requestAnimationFrame(loop);
  }
  loop();

  window.addEventListener('resize', resize);

  return {
    setProgress(v: number) {
      progress = Math.min(1, Math.max(0, v));
    },
    setPointer(nx: number, ny: number) {
      pointerTarget.set(nx, ny);
    },
    resize,
    reducedMotion,
  };
}
