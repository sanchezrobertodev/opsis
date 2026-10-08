# wan-core: RunPod serverless worker for ai-media-studio's open Wan models.
# Base: worker-comfyui 5.10.0 = ComfyUI 0.34.0 (the same version as the local VideoStudio install the workflows
# were checked against), torch 2.11. Every node the workflows use is built into ComfyUI 0.34, so no custom nodes.
# Models live on the network volume; the image adds the CUDA 13 torch below and a few KB of files.
FROM runpod/worker-comfyui:5.10.0-base

# 0.3.0: the same torch rebuilt for CUDA 13. ComfyUI only turns on comfy-kitchen's CUDA kernels (the fp8 and int8
# matmuls every wan-core model uses) with a cu130 torch; the endpoint needs hosts whose driver supports CUDA 13.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu130 \
        torch==2.11.0+cu130 torchvision==0.26.0+cu130 torchaudio==2.11.0+cu130

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY handler.py fetch_models.py models.yaml /

ENV WAN_CORE_MODELS=/runpod-volume/models \
    COMFY_LOG_LEVEL=INFO
