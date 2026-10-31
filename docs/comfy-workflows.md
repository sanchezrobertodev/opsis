# Add your own ComfyUI workflow

Any graph that runs in your ComfyUI can become a model in Opsis: it shows up in the Create form with its own
fields, runs free on your GPU, and gets the same file checks, progress and library as the built-in local models.
A model is three files: the graph, a map saying which node inputs Opsis fills, and a model YAML. A script drafts
all three from the graph.

## 1. Get the graph ready in ComfyUI

- Make it work in ComfyUI first, with the models you want.
- Bring media in with the standard loaders: `LoadImage`, `LoadVideo` (or VideoHelperSuite's `VHS_LoadVideo`) and
  `LoadAudio`. Opsis uploads each input and puts its name in the loader.
- End it with a save node: `SaveImage`, `SaveVideo` (or `VHS_VideoCombine`) or `SaveAudio`. Preview nodes are
  ignored.
- Save it with **Workflow > Export (API)**. The ordinary save is the editor's format, which the script refuses.

## 2. Draft the model

```bash
uv run python scripts/import_comfy_workflow.py my_workflow_api.json --id my-wan-lora --name "My Wan LoRA"
```

Add `--dry-run` to print the drafts first, and `--min-vram-gb 16` if smaller cards can't run it (they then skip
the route and say so). It writes:

| File | What it is |
|---|---|
| `workflows/comfy/my_wan_lora.json` | The graph, as exported. |
| `workflows/comfy/my_wan_lora.map.yaml` | Which node inputs each field fills. |
| `registry/models/<video, image or audio>/my-wan-lora.yaml` | The model: its fields, mode and a free `comfy_local` route. |

It then loads the registry and prints any lint errors for the new model. The API and the worker pick up new and
edited YAML within a second, so there's nothing to restart.

## 3. Check what it guessed

The script goes by input names and links, so it reads most graphs right but can't know your intent. Its notes,
printed and kept at the top of the map, say what it guessed by order.

**The map** (`.map.yaml`). Each key lists the node inputs it fills:

| Key | Filled with | Guessed from |
|---|---|---|
| `prompt` | The prompt | Text that feeds a `positive` input |
| `negative_prompt` | The negative prompt; `mode: append` adds it to text the graph already has | Text that feeds a `negative` input |
| `seed` | A new seed per take, unless the form sets one | `seed`, `noise_seed` |
| `steps` | Steps | `steps`, except on samplers that run part of the steps |
| `width`, `height` | Size | The latent's size, and every other node that repeats it (resizes, schedulers) |
| `length` | Frames | The latent's frame count and its repeats (an audio latent's `frames_number`) |
| `fps` | Frame rate | `CreateVideo` or `VHS_VideoCombine` and its repeats |
| `start_image`, `end_image`, `input_video`, `mask`, ... | An uploaded file | A loader, named by the input it feeds, else by order; `mask` when the file name says so |
| `filename_prefix` | Where ComfyUI saves the result | Save nodes |
| `lora`, `lora_strength` | A LoRA file picked in the form, and its strength | The graph's one `LoraLoaderModelOnly` or `LoraLoader` |

Fix a wrong guess by editing the node ids. Two more things the map can say:

- `optional:` drops a node when its input is missing, and unlinks it from the nodes it feeds. The built-in Wan 5B
  graph uses this to run text-to-video without its start frame:
  `optional: {start_image: {node: "56", unlink: [{node: "55", input: start_image}]}}`. A node in the middle of a
  chain uses `bypass` instead: with no LoRA picked, `optional: {lora: {node: "90", bypass: [model]}}` drops the
  loader and links what it fed to its own `model` input (a `LoraLoader` passes `[model, clip]`).
- `outputs:` lists the save nodes and what they make.

**The model** (`registry/models/.../<id>.yaml`):

- `name` and `description` show in the model picker; `status: preview` marks it as new.
- `license_tier`: set it from the weights' license (`open_permissive` for Apache 2.0 or MIT, `open_restricted` or
  `non_commercial` otherwise).
- The mode's `ops` decide where it shows: `t2v`, `i2v` and `flf2v` in Create video, `t2i` and `i2i` in Create
  image, `restyle` and the other edit ops in Edit video, `motion_transfer` in Motion transfer
  (`apps/web/src/lib/studioKinds.ts`). Video in, video out is drafted as `restyle`.
- Parameters: the draft exposes duration (turned into frames at the graph's frame rate), width, height, steps,
  seed, and a LoRA when the graph has one loader. The LoRA field (`comfy_choices: LoraLoaderModelOnly.lora_name`)
  lists the files in your ComfyUI's `loras` folder when the form opens, and defaults to the one the graph was saved
  with; graphs with several loaders keep them as saved. Rename labels, change ranges, or swap width and height for an aspect ratio with a transform, as the
  built-in models do (`studio/providers/transforms/comfy.py`).
- `eta` is the expected run time in seconds; time a run and replace the draft's guess. It can use the params, e.g.
  `"40 * duration * steps / 20"`.
- `examples` keeps one free `smoke` example for the contract test.

The CLAUDE.md section "Registry expressions and mapping" lists everything a model YAML can do.

## 4. Model files (optional)

Before a job, Opsis asks ComfyUI which files its loaders can see and names any the graph needs that are missing.
To have that message say where to get a file, add it to `infra/runpod/wan-core/models.yaml` with a group of your
own and `local_only: true` (so it never goes onto the RunPod volume):

```yaml
  - {group: my-lora, local_only: true, folder: loras, name: my_style.safetensors, bytes: 306470448,
     url: "https://huggingface.co/you/my-style/resolve/main/my_style.safetensors"}
```

`fetch_models.py --local --dest <ComfyUI>/models --group my-lora` then downloads it, checking the size.

## 5. Try it and test it

Start ComfyUI (Opsis looks for it at `COMFYUI_URL`, default `http://127.0.0.1:8188`), open the studio its ops
belong to and pick the model. If ComfyUI lacks a node class or file, the route says which instead of failing
mid-run. Then:

```bash
uv run pytest apps/server/tests/contract -k my-wan-lora
```

To share it, open a pull request with the three files.
