"use client";

import { Environment, Grid, OrbitControls, PerspectiveCamera, TransformControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { type MutableRefObject, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { ENVIRONMENT_DEFAULT, evaluate, fovFor, type SceneDoc, type SceneObject, trackOf, type Transform, type Vec3 } from "@/lib/jutsu3d";
import { SparkLayer } from "@/components/three/Splat";
import { ObjectNode } from "./SceneObjects";

export type GizmoMode = "translate" | "rotate" | "scale";
/** What render passes need from the live canvas. */
export type ViewportHandle = {
  gl: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.Camera;
  setFrameloop: (mode: "always" | "demand" | "never") => void;
};

export function Viewport({ doc, selected, time, mode, look, helpers = true, onSelect, onCommit, handleRef }: {
  doc: SceneDoc; selected: string | null; time: number | null; mode: GizmoMode; look: "free" | "camera";
  helpers?: boolean; onSelect: (id: string | null) => void; onCommit: (id: string, t: Transform) => void;
  handleRef?: MutableRefObject<ViewportHandle | null>;
}) {
  const nodes = useRef(new Map<string, THREE.Group>());
  const [target, setTarget] = useState<THREE.Group | null>(null);
  const dragging = useRef(false);
  const shown = doc.objects.map((o) => evaluate(o, trackOf(doc, o.id), time));
  const shot = shown.find((o) => o.id === doc.active_camera_id && o.kind === "camera");
  const throughCamera = look === "camera" && Boolean(shot);

  useEffect(() => {
    setTarget(selected ? nodes.current.get(selected) ?? null : null);
  }, [selected, doc.objects]);

  return (
    <Canvas shadows dpr={[1, 2]} gl={{ preserveDrawingBuffer: true, antialias: true }}
            camera={{ position: [6, 4, 8], fov: 45, near: 0.05, far: 500 }}
            onPointerMissed={() => { if (!dragging.current) onSelect(null); }}>
      <color attach="background" args={[doc.settings.background]} />
      {doc.objects.some((o) => o.kind === "world") && <SparkLayer />}
      <ambientLight intensity={doc.settings.ambient} />
      <StudioRoom intensity={doc.settings.environment ?? ENVIRONMENT_DEFAULT} />
      {helpers && <Grid userData={{ helper: true }} infiniteGrid cellSize={0.5} sectionSize={2.5} cellColor="#36373c" sectionColor="#45464c" fadeDistance={45} />}
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[200, 200]} />
        <shadowMaterial opacity={0.35} />
      </mesh>
      {shown.map((o) => (
        <ObjectNode key={o.id} o={o} selected={helpers && o.id === selected} time={time}
                    helpers={helpers && !(throughCamera && o.id === shot?.id)} // not the camera you look through
                    onSelect={() => onSelect(o.id)}
                    ref={(g) => { if (g) nodes.current.set(o.id, g); else nodes.current.delete(o.id); }} />
      ))}
      {throughCamera && shot ? <ShotCamera o={shot} /> : (
        <OrbitControls makeDefault enableDamping={false} target={[0, 1, 0]} />
      )}
      {helpers && !throughCamera && target && selected && (
        <TransformControls object={target} mode={mode} size={0.8}
                           onMouseDown={() => { dragging.current = true; }}
                           onMouseUp={() => {
                             const g = nodes.current.get(selected);
                             if (g) onCommit(selected, toTransform(g));
                             setTimeout(() => { dragging.current = false; }, 0);
                           }} />
      )}
      {handleRef && <Expose handleRef={handleRef} />}
    </Canvas>
  );
}

function toTransform(g: THREE.Object3D): Transform {
  const r = (n: number) => Math.round(n * 1000) / 1000;
  return {
    position: g.position.toArray().map(r) as Vec3,
    rotation: [r(g.rotation.x), r(g.rotation.y), r(g.rotation.z)],
    scale: g.scale.toArray().map(r) as Vec3,
  };
}

/** One studio room per renderer, generated here (nothing is fetched); it goes when the renderer's context does. */
const rooms = new WeakMap<THREE.WebGLRenderer, THREE.Texture>();
function roomFor(gl: THREE.WebGLRenderer) {
  let map = rooms.get(gl);
  if (!map) {
    const pmrem = new THREE.PMREMGenerator(gl);
    const room = new RoomEnvironment();
    map = pmrem.fromScene(room, 0.04).texture;
    room.dispose();
    pmrem.dispose();
    rooms.set(gl, map);
  }
  return map;
}

/**
 * A soft studio room as the scene's image light, so metallic PBR models from picture-to-3D services reflect it
 * instead of going black. It lights, never shows: the background setting stays. Data passes override materials,
 * so it never reaches depth or normals.
 */
function StudioRoom({ intensity }: { intensity: number }) {
  const gl = useThree((s) => s.gl);
  const [, setRestored] = useState(0);
  useEffect(() => {
    // a lost context takes the room's pixels with it (a render target has none to upload again): draw it anew
    const redraw = () => { rooms.delete(gl); setRestored((n) => n + 1); };
    gl.domElement.addEventListener("webglcontextrestored", redraw);
    return () => gl.domElement.removeEventListener("webglcontextrestored", redraw);
  }, [gl]);
  return <Environment map={roomFor(gl)} environmentIntensity={intensity} />;
}

/** Looks through the scene's active camera: its position, target and focal length. */
function ShotCamera({ o }: { o: SceneObject }) {
  const cam = useRef<THREE.PerspectiveCamera>(null);
  useFrame(() => {
    cam.current?.position.set(...o.transform.position);
    cam.current?.lookAt(...o.target);
  });
  return <PerspectiveCamera ref={cam} makeDefault fov={fovFor(o.focal_length)} near={0.05} far={500}
                            position={o.transform.position} />;
}

function Expose({ handleRef }: { handleRef: MutableRefObject<ViewportHandle | null> }) {
  const { gl, scene, camera, setFrameloop } = useThree();
  useEffect(() => {
    handleRef.current = { gl, scene, camera, setFrameloop };
    return () => { handleRef.current = null; };
  }, [gl, scene, camera, setFrameloop, handleRef]);
  return null;
}
