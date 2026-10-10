"use client";

import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

/**
 * A soft studio room drawn on this renderer (nothing is fetched) and prefiltered for PBR. Set as a scene's
 * `environment`, it gives metallic models from picture-to-3D services something to reflect instead of going black.
 * The caller owns the texture; it lives only as long as the renderer's context.
 */
export function studioRoom(gl: THREE.WebGLRenderer): THREE.Texture {
  const pmrem = new THREE.PMREMGenerator(gl);
  const room = new RoomEnvironment();
  const map = pmrem.fromScene(room, 0.04).texture;
  room.dispose();
  pmrem.dispose();
  return map;
}
