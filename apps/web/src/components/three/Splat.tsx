"use client";

import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";
import { useThree } from "@react-three/fiber";
import { useEffect, useState } from "react";
import type * as THREE from "three";

/** World Labs splats (and most 3DGS files) use OpenCV axes, y down and z forward; this turns them y up. */
export const OPENCV_TO_Y_UP: [number, number, number] = [Math.PI, 0, 0];

/** Disposes `obj` after React lets go of it for good (StrictMode re-runs effects without removing the object). */
function disposeWhenDetached(obj: THREE.Object3D & { dispose: () => void }) {
  setTimeout(() => { if (!obj.parent) obj.dispose(); }, 0);
}

/** Spark's splat renderer for this canvas. Mount one wherever splats are shown. */
export function SparkLayer() {
  const gl = useThree((s) => s.gl);
  const [spark] = useState(() => new SparkRenderer({ renderer: gl }));
  useEffect(() => () => disposeWhenDetached(spark), [spark]);
  return <primitive object={spark} />;
}

/** The SparkRenderer in a scene, if any (render passes settle its sort before each frame). */
export function sparkIn(scene: THREE.Scene): SparkRenderer | null {
  let found: SparkRenderer | null = null;
  scene.traverse((n) => { if (!found && n instanceof SparkRenderer) found = n; });
  return found;
}

/** A splat file (.spz or .ply) by URL. Key it by URL: a new file is a new mesh. */
export function Splat({ url, onLoad }: { url: string; onLoad?: (mesh: SplatMesh) => void }) {
  const [mesh] = useState(() => {
    const m = new SplatMesh({ url });
    m.userData.splat = true; // render passes hide splats where they draw data instead of pictures
    return m;
  });
  useEffect(() => {
    let live = true;
    void mesh.initialized.then((m) => { if (live) onLoad?.(m); });
    return () => { live = false; disposeWhenDetached(mesh); };
  }, [mesh, onLoad]);
  return <primitive object={mesh} />;
}
