"use client";

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { api, API_URL, type Asset, mediaUrl, unwrap } from "./api";
import { studioRoom } from "./room";

const SIZE = 384;
const ROOM_LIGHT = 0.4;

function blobOf(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("couldn't read the picture"))), "image/webp", 0.9));
}

/** A three-quarter view of a GLB on a dark ground, for the library (the server can't draw 3D files). */
export async function modelThumbnail(url: string): Promise<Blob> {
  const gltf = await new GLTFLoader().loadAsync(url);
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  const room = studioRoom(renderer);
  try {
    renderer.setSize(SIZE, SIZE, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#1c1d20");
    // metal from picture-to-3D services reflects the room instead of going black
    scene.environment = room;
    scene.environmentIntensity = ROOM_LIGHT;
    scene.add(new THREE.HemisphereLight("#ffffff", "#2a2a2e", 2.2));
    const sun = new THREE.DirectionalLight("#fff4e0", 2.5);
    sun.position.set(3, 5, 4);
    scene.add(sun, gltf.scene);
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    const distance = (sphere.radius || 1) / Math.sin((35 / 2) * (Math.PI / 180));
    camera.position.copy(sphere.center).add(new THREE.Vector3(0.6, 0.45, 0.75).normalize().multiplyScalar(distance));
    camera.lookAt(sphere.center);
    renderer.render(scene, camera);
    return await blobOf(renderer.domElement);
  } finally {
    room.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }
}

/**
 * Draws every GLB's library picture again (with today's lighting), and the missing ones. A world's collision mesh
 * keeps a picture its world gave it, but one with none (Marble's never come with one) gets drawn like any model. A
 * model that won't load is counted and skipped.
 */
export async function redrawModelThumbnails(onProgress: (done: number, total: number) => void) {
  const models: Asset[] = [];
  let cursor: string | undefined;
  do {
    const page = await unwrap(api.GET("/assets", { params: { query: { kind: "model3d", limit: 200, cursor } } }));
    models.push(...page.items.filter((a) => a.metadata?.part !== "collider" || !a.thumb_url));
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  let failed = 0;
  onProgress(0, models.length);
  for (const [i, a] of models.entries()) {
    try {
      await saveThumbnail(a.id, await modelThumbnail(mediaUrl(a.url) ?? a.url));
    } catch {
      failed += 1;
    }
    onProgress(i + 1, models.length);
  }
  return { redrawn: models.length - failed, failed };
}

/** Store a picture of a canvas or model as an asset's thumbnail. */
export async function saveThumbnail(assetId: string, picture: Blob): Promise<Asset> {
  const form = new FormData();
  form.append("file", picture, "thumb.webp");
  const r = await fetch(`${API_URL}/assets/${assetId}/thumbnail`, { method: "POST", body: form });
  if (!r.ok) throw new Error(`couldn't save the thumbnail (${r.status})`);
  return r.json();
}

/** The canvas's current picture (it needs `preserveDrawingBuffer`), scaled to fit `max` pixels. */
export function canvasPicture(canvas: HTMLCanvasElement, max = 1920, type = "image/png"): Promise<Blob> {
  const scale = Math.min(1, max / Math.max(canvas.width, canvas.height));
  const out = document.createElement("canvas");
  out.width = Math.round(canvas.width * scale);
  out.height = Math.round(canvas.height * scale);
  out.getContext("2d")?.drawImage(canvas, 0, 0, out.width, out.height);
  return new Promise((resolve, reject) =>
    out.toBlob((b) => (b ? resolve(b) : reject(new Error("couldn't read the view"))), type, 0.92));
}
