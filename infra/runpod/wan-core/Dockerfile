# wan-core: RunPod serverless worker for ai-media-studio's open Wan models.
# Base: worker-comfyui 5.10.0 = ComfyUI 0.34.0 (the same version as the local VideoStudio install the workflows
# were checked against), CUDA 12.8.1, torch 2.11 (cu128, runs on Blackwell too). Every node the workflows use is
# built into ComfyUI 0.34, so no custom nodes. Models live on the network volume, so the image adds only a few KB.
FROM runpod/worker-comfyui:5.10.0-base

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY handler.py fetch_models.py models.yaml /

ENV WAN_CORE_MODELS=/runpod-volume/models \
    COMFY_LOG_LEVEL=INFO
