# Local ComfyUI model storage

AI Media Studio's Settings > Local model storage lets the user choose an absolute model folder on the
API server's computer. Saving creates the model-type subfolders and checks write access. It changes future
downloads after ComfyUI restarts; existing files are neither moved nor deleted. Previous locations remain
in the shared YAML as additional model libraries.

Browser downloads still use the browser's Save As location. Extensions with independent caches may have
their own settings. This does not affect cloud providers or RunPod storage.

## Connect a local installation

Set `COMFYUI_MODEL_PATHS_CONFIG` in the API server's `.env` to a YAML file shared with the ComfyUI launcher.
`COMFYUI_URL` must point to a loopback address on the same machine. Restart the API after changing `.env`.

The YAML needs a `studio_models` group containing at least:

```yaml
studio_models:
  base_path: G:/AI/Models/ComfyUI
  is_default: true
  checkpoints: checkpoints
```

Existing groups can remain in the file. Save the folder through Settings to populate all supported model
categories. The managed group is written last so its paths take default priority.

The launcher must read `studio_models.base_path` each time it starts and pass both
`--models-directory <base_path>` and `--extra-model-paths-config <shared-yaml>` to ComfyUI. For a Python
launcher with PyYAML installed:

```python
model_paths = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
model_directory = model_paths["studio_models"]["base_path"]
# Add these arguments to the existing ComfyUI command:
# --models-directory model_directory --extra-model-paths-config str(config_path)
```

The Settings panel compares the saved directory with the running ComfyUI command line, showing a restart
notice until they match. If ComfyUI is offline or does not report its directory, it displays an unknown
active state. Saving does not interrupt queued or running generations.

On the current Windows installation the shared file is `C:/Dev/AI/VideoStudio/setup/models.yaml` and the
launcher is `C:/Dev/AI/VideoStudio/setup/launcher.py`. The original model folders on C: are junctions to G:;
scripts using those old paths continue writing to G:. A later settings change does not retarget those
junctions or change scripts that explicitly name another path.
