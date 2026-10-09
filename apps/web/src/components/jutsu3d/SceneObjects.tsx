"use client";

import { useGLTF } from "@react-three/drei";
import { type ThreeEvent, useFrame } from "@react-three/fiber";
import { forwardRef, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { clone as cloneRigged } from "three/examples/jsm/utils/SkeletonUtils.js";
import { mediaUrl } from "@/lib/api";
import { type Animation, clipTime, type SceneObject, type Vec3 } from "@/lib/jutsu3d";
import { useAsset } from "@/lib/queries";
import { OPENCV_TO_Y_UP, Splat } from "@/components/three/Splat";

type Props = {
  o: SceneObject; selected: boolean; onSelect: (e: ThreeEvent<MouseEvent>) => void; helpers: boolean;
  /** The timeline time a model's clip is posed at (null: its first pose). */
  time?: number | null;
};

const SEL = "#f0a93b";

/** One object of the scene: a group at its transform holding what it looks like. */
export const ObjectNode = forwardRef<THREE.Group, Props>(function ObjectNode({ o, selected, onSelect, helpers, time = null }, ref) {
  const { position, rotation, scale } = o.transform;
  return (
    <group ref={ref} name={o.id} position={position} rotation={rotation} scale={scale} visible={o.visible}
           onClick={(e) => { e.stopPropagation(); onSelect(e); }}>
      <Body o={o} selected={selected} helpers={helpers} time={time} />
    </group>
  );
});

function Body({ o, selected, helpers, time }: { o: SceneObject; selected: boolean; helpers: boolean; time: number | null }) {
  const material = <meshStandardMaterial color={o.color} roughness={0.7} emissive={selected ? SEL : "#000000"}
                                         emissiveIntensity={selected ? 0.18 : 0} />;
  switch (o.kind) {
    case "box": return <mesh castShadow receiveShadow><boxGeometry args={[1, 1, 1]} />{material}</mesh>;
    case "sphere": return <mesh castShadow receiveShadow><sphereGeometry args={[0.5, 32, 16]} />{material}</mesh>;
    case "cylinder": return <mesh castShadow receiveShadow><cylinderGeometry args={[0.5, 0.5, 1, 32]} />{material}</mesh>;
    case "cone": return <mesh castShadow receiveShadow><coneGeometry args={[0.5, 1, 32]} />{material}</mesh>;
    case "plane":
      return <mesh receiveShadow rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[4, 4]} />{material}</mesh>;
    case "mannequin": return <Mannequin pose={o.pose} color={o.color} selected={selected} />;
    case "model": return o.asset_id ? (
      <Suspense fallback={<Placeholder />}><Model assetId={o.asset_id} animation={o.animation ?? null} time={time} /></Suspense>
    ) : null;
    case "world": return o.asset_id ? <World splatId={o.asset_id} colliderId={o.collider_asset_id ?? null} /> : null;
    case "sun": case "point_light": case "spot_light": return <LightNode o={o} helpers={helpers} selected={selected} />;
    case "camera": return helpers ? <CameraGlyph o={o} selected={selected} /> : null;
  }
}

function Placeholder() {
  return <mesh><boxGeometry args={[0.4, 0.4, 0.4]} /><meshBasicMaterial color="#45464c" wireframe /></mesh>;
}

function Model({ assetId, animation, time }: { assetId: string; animation: Animation | null; time: number | null }) {
  const { data: asset } = useAsset(assetId);
  const { data: source } = useAsset(animation?.source_asset_id ?? undefined);
  const url = asset ? mediaUrl(asset.url) : undefined;
  if (!url) return <Placeholder />;
  if (!animation) return <Gltf url={url} />;
  const clipUrl = animation.source_asset_id ? (source ? mediaUrl(source.url) : undefined) : url;
  return clipUrl ? <Animated url={url} clipUrl={clipUrl} animation={animation} time={time} /> : <Gltf url={url} />;
}

/**
 * A rigged model posed at the timeline time by a clip, from its own file or another on the same skeleton (bones are
 * matched by name). The copy carries `userData.seek`, so the render passes pose it for each frame they draw.
 */
function Animated({ url, clipUrl, animation, time }: { url: string; clipUrl: string; animation: Animation; time: number | null }) {
  const copy = useCopy(url, true);
  const { animations } = useGLTF(clipUrl);
  const clip = animations.find((a) => a.name === animation.clip) ?? null;
  const mixer = useMemo(() => new THREE.AnimationMixer(copy), [copy]);
  const action = useMemo(() => {
    if (!clip) return null;
    return paused(mixer.clipAction(clip)); // the timeline sets its time
  }, [mixer, clip]);
  useEffect(() => () => { mixer.stopAllAction(); mixer.uncacheRoot(copy); }, [mixer, copy]);
  // a clip that's switched away from stops before the new one poses the rig, so the two never blend
  useLayoutEffect(() => () => { if (action) retire(mixer, action); }, [mixer, action]);
  const seek = useCallback((t: number | null) => {
    if (action && clip) pose(mixer, action, clipTime(animation, t ?? 0, clip.duration));
  }, [action, clip, mixer, animation]);
  useLayoutEffect(() => { seek(time); mark(copy, { at: time }); }, [seek, time, copy]);
  useLayoutEffect(() => {
    mark(copy, { seek });
    return () => mark(copy, { seek: undefined });
  }, [copy, seek]);
  return <primitive object={copy} />;
}

// three.js objects are posed in place, outside React's state
function pose(mixer: THREE.AnimationMixer, action: THREE.AnimationAction, seconds: number) {
  action.time = seconds;
  mixer.update(0);
}

function mark(node: THREE.Object3D, data: Record<string, unknown>) {
  Object.assign(node.userData, data);
}

function paused(action: THREE.AnimationAction): THREE.AnimationAction {
  // the timeline loops a clip itself (clipTime); left repeating, three.js would wrap its last frame to its first
  action.setLoop(THREE.LoopOnce, 1);
  action.clampWhenFinished = true;
  action.play();
  action.paused = true;
  return action;
}

function retire(mixer: THREE.AnimationMixer, action: THREE.AnimationAction) {
  action.stop();
  mixer.uncacheAction(action.getClip());
}

/**
 * A Gaussian-splat world. Its collision mesh, when it has one, stays hidden: the depth pass draws it in place of the
 * splats (which have no surface to measure).
 */
function World({ splatId, colliderId }: { splatId: string; colliderId: string | null }) {
  const { data: splat } = useAsset(splatId);
  const { data: collider } = useAsset(colliderId ?? undefined);
  const url = splat ? mediaUrl(splat.url) : undefined;
  const mesh = collider ? mediaUrl(collider.url) : undefined;
  return (
    <group rotation={OPENCV_TO_Y_UP}>
      {url && <Splat key={url} url={url} />}
      {mesh && (
        <group visible={false} userData={{ depthOnly: true }}>
          <Suspense fallback={null}><Gltf url={mesh} shadows={false} /></Suspense>
        </group>
      )}
    </group>
  );
}

function Gltf({ url, shadows = true }: { url: string; shadows?: boolean }) {
  return <primitive object={useCopy(url, shadows)} />;
}

/** One loaded file can be in the scene several times: each use gets its own copy, a rig's bones rebound to it. */
function useCopy(url: string, shadows: boolean): THREE.Object3D {
  const { scene } = useGLTF(url);
  return useMemo(() => {
    const c = cloneRigged(scene);
    c.traverse((n) => { if ((n as THREE.Mesh).isMesh) { n.castShadow = shadows; n.receiveShadow = shadows; } });
    return c;
  }, [scene, shadows]);
}

// ---------------------------------------------------------------- the figure

const LIMB = 0.055;

function Bone({ length, radius = LIMB, color, selected }: { length: number; radius?: number; color: string; selected: boolean }) {
  return (
    <mesh castShadow position={[0, -length / 2, 0]}>
      <capsuleGeometry args={[radius, Math.max(0.01, length - radius * 2), 6, 12]} />
      <meshStandardMaterial color={color} roughness={0.8} emissive={selected ? SEL : "#000000"} emissiveIntensity={selected ? 0.18 : 0} />
    </mesh>
  );
}

/** A 1.75 m wooden figure facing +z, posed by joint rotations (radians). */
function Mannequin({ pose, color, selected }: { pose: Record<string, Vec3>; color: string; selected: boolean }) {
  const j = (name: string): Vec3 => pose[name] ?? [0, 0, 0];
  const limb = (len: number, r?: number) => <Bone length={len} radius={r} color={color} selected={selected} />;
  return (
    <group position={[0, 0.95, 0]}>
      {/* pelvis */}
      <mesh castShadow><boxGeometry args={[0.3, 0.12, 0.16]} /><meshStandardMaterial color={color} roughness={0.8} /></mesh>
      <group rotation={j("spine")}>
        {/* bones hang down from their joint; flipped, the torso stands up from the pelvis to the shoulders */}
        <group rotation={[Math.PI, 0, 0]}>{limb(0.5, 0.11)}</group>
        <group position={[0, 0.5, 0]} rotation={j("neck")}>
          <group rotation={[Math.PI, 0, 0]}>{limb(0.12, 0.045)}</group>
          <group position={[0, 0.2, 0]} rotation={j("head")}>
            <mesh castShadow scale={[0.9, 1.1, 1]}><sphereGeometry args={[0.11, 24, 16]} />
              <meshStandardMaterial color={color} roughness={0.8} emissive={selected ? SEL : "#000000"} emissiveIntensity={selected ? 0.18 : 0} /></mesh>
            {/* a nose, so the figure's facing is readable */}
            <mesh position={[0, 0, 0.1]}><coneGeometry args={[0.02, 0.05, 8]} /><meshStandardMaterial color={color} /></mesh>
          </group>
        </group>
        {(["l", "r"] as const).map((side) => (
          <group key={side} position={[side === "l" ? 0.21 : -0.21, 0.45, 0]} rotation={j(`shoulder_${side}`)}>
            {limb(0.3)}
            <group position={[0, -0.3, 0]} rotation={j(`elbow_${side}`)}>
              {limb(0.27, 0.045)}
              <mesh castShadow position={[0, -0.31, 0]}><sphereGeometry args={[0.045, 12, 8]} /><meshStandardMaterial color={color} /></mesh>
            </group>
          </group>
        ))}
      </group>
      {(["l", "r"] as const).map((side) => (
        <group key={side} position={[side === "l" ? 0.1 : -0.1, -0.05, 0]} rotation={j(`hip_${side}`)}>
          {limb(0.45, 0.07)}
          <group position={[0, -0.45, 0]} rotation={j(`knee_${side}`)}>
            {limb(0.42, 0.055)}
            <mesh castShadow position={[0, -0.44, 0.06]}><boxGeometry args={[0.09, 0.05, 0.22]} /><meshStandardMaterial color={color} /></mesh>
          </group>
        </group>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------- lights and cameras

function LightNode({ o, helpers, selected }: { o: SceneObject; helpers: boolean; selected: boolean }) {
  const light = useRef<THREE.DirectionalLight | THREE.SpotLight>(null);
  const target = useMemo(() => new THREE.Object3D(), []);
  useLayoutEffect(() => {
    if (!light.current) return;
    light.current.target = target;
  }, [target]);
  // aim at the target in world space, wherever the light's group is
  useFrame(() => {
    const parent = light.current?.parent;
    if (!parent) return;
    const goal = o.kind === "sun" ? new THREE.Vector3(0, 0, 0) : new THREE.Vector3(...o.target);
    target.position.copy(parent.worldToLocal(goal.clone()));
    target.updateMatrixWorld();
  });
  return (
    <>
      {o.kind === "sun" && (
        <directionalLight ref={light as React.RefObject<THREE.DirectionalLight>} color={o.color} intensity={o.intensity}
                          castShadow shadow-mapSize={[2048, 2048]} shadow-camera-left={-12} shadow-camera-right={12}
                          shadow-camera-top={12} shadow-camera-bottom={-12} />
      )}
      {o.kind === "point_light" && <pointLight color={o.color} intensity={o.intensity} distance={0} decay={2} castShadow />}
      {o.kind === "spot_light" && (
        <spotLight ref={light as React.RefObject<THREE.SpotLight>} color={o.color} intensity={o.intensity} angle={0.5}
                   penumbra={0.4} distance={0} decay={2} castShadow />
      )}
      <primitive object={target} />
      {helpers && (
        <mesh userData={{ helper: true }}>
          <sphereGeometry args={[0.12, 16, 8]} />
          <meshBasicMaterial color={selected ? SEL : o.color} />
        </mesh>
      )}
    </>
  );
}

function CameraGlyph({ o, selected }: { o: SceneObject; selected: boolean }) {
  const body = useRef<THREE.Group>(null);
  useFrame(() => {
    const g = body.current;
    if (!g?.parent) return;
    g.lookAt(new THREE.Vector3(...o.target)); // aims in world space, like the real camera
  });
  const color = selected ? SEL : "#a3a39e";
  return (
    <group ref={body} userData={{ helper: true }}>
      <mesh><boxGeometry args={[0.22, 0.16, 0.3]} /><meshBasicMaterial color={color} wireframe /></mesh>
      <mesh position={[0, 0, 0.25]} rotation={[-Math.PI / 2, 0, 0]}>
        <coneGeometry args={[0.12, 0.2, 4, 1, true]} /><meshBasicMaterial color={color} wireframe />
      </mesh>
    </group>
  );
}

