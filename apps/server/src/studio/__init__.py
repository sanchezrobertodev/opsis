"""Opsis server package."""

import mimetypes

# 3D files the studio stores and serves (GLB models, Gaussian-splat worlds)
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("application/x-ply", ".ply")
mimetypes.add_type("application/octet-stream", ".spz")
