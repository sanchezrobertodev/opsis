"use client";

import { OrbitControls, PerspectiveCamera, useTexture } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { type MutableRefObject, Suspense, useCallback, useEffect, useState } from "react";
import * as THREE from "three";
import { OPENCV_TO_Y_UP, SparkLayer, Splat } from "@/components/three/Splat";

/** What the page needs from a viewer: its canvas, to grab the current view. */
export type ViewerHandle = { canvas: HTMLCanvasElement; render: () => void };

function Expose({ handleRef }: { handleRef: MutableRefObject<ViewerHandle | null> }) {
  const { gl, scene, camera } = useThree();
  useEffect(() => {
    handleRef.current = { canvas: gl.domElement, render: () => gl.render(scene, camera) };
    return () => { handleRef.current = null; };
  }, [gl, scene, camera, handleRef]);
  return null;
}

/** Scroll to zoom by changing the lens (moving would leave the spot the world or panorama was made from). */
function useLensZoom(initial = 60) {
  const [fov, setFov] = useState(initial);
  const onWheel = useCallback((e: React.WheelEvent) => {
    setFov((f) => Math.min(100, Math.max(20, f + Math.sign(e.deltaY) * 4)));
  }, []);
  return { fov, onWheel };
}

/**
 * An explorable Gaussian-splat world, seen from where it was made (the origin). Drag to look around, right-drag
 * to move, scroll to zoom.
 */
export function WorldViewer({ url, handleRef, onReady }: {
  url: string; handleRef: MutableRefObject<ViewerHandle | null>; onReady?: () => void;
}) {
  const { fov, onWheel } = useLensZoom();
  const [loaded, setLoaded] = useState(false);
  const ready = useCallback(() => { setLoaded(true); onReady?.(); }, [onReady]);
  return (
    <div className="relative h-full w-full" onWheel={onWheel}>
      <Canvas gl={{ preserveDrawingBuffer: true, antialias: false }} dpr={[1, 2]}>
        <color attach="background" args={["#0d0e10"]} />
        <PerspectiveCamera makeDefault position={[0, 0, 0.01]} fov={fov} near={0.01} far={1000} />
        <OrbitControls makeDefault target={[0, 0, 0]} enableZoom={false} rotateSpeed={-0.35} enableDamping={false} />
        <SparkLayer />
        <group rotation={OPENCV_TO_Y_UP}>
          <Splat key={url} url={url} onLoad={ready} />
        </group>
        <Expose handleRef={handleRef} />
      </Canvas>
      {!loaded && <p className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-muted">Loading the world…</p>}
    </div>
  );
}

function PanoSphere({ url, onReady }: { url: string; onReady?: () => void }) {
  const texture = useTexture(url, (t) => { t.colorSpace = THREE.SRGBColorSpace; });
  useEffect(() => { onReady?.(); }, [texture, onReady]);
  return (
    // seen from inside, so the sphere is mirrored to read the right way round
    <mesh scale={[-1, 1, 1]}>
      <sphereGeometry args={[50, 96, 48]} />
      <meshBasicMaterial map={texture} side={THREE.BackSide} toneMapped={false} />
    </mesh>
  );
}

/** A 360 panorama from its centre. Drag to look around, scroll to zoom. */
export function PanoViewer({ url, handleRef, onReady }: {
  url: string; handleRef: MutableRefObject<ViewerHandle | null>; onReady?: () => void;
}) {
  const { fov, onWheel } = useLensZoom(70);
  return (
    <div className="h-full w-full" onWheel={onWheel}>
      <Canvas gl={{ preserveDrawingBuffer: true }} dpr={[1, 2]}>
        <PerspectiveCamera makeDefault position={[0, 0, 0.01]} fov={fov} near={0.1} far={200} />
        <OrbitControls makeDefault target={[0, 0, 0]} enableZoom={false} enablePan={false} rotateSpeed={-0.35}
                       enableDamping={false} />
        <Suspense fallback={null}><PanoSphere url={url} onReady={onReady} /></Suspense>
        <Expose handleRef={handleRef} />
      </Canvas>
    </div>
  );
}
