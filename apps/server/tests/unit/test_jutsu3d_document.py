import pytest
from pydantic import ValidationError

from studio.jutsu3d.document import Jutsu3dDocument, SceneObject, starter_scene


def scene_with_world(collider: str | None = "c-1") -> Jutsu3dDocument:
    doc = Jutsu3dDocument.model_validate(starter_scene())
    world = SceneObject(id="wworld", kind="world", asset_id="s-1", collider_asset_id=collider)
    return doc.model_copy(update={"objects": [world, *doc.objects]})


def test_a_world_needs_its_splat_and_lists_its_collider():
    with pytest.raises(ValidationError, match="needs its splat asset"):
        SceneObject(kind="world")
    assert scene_with_world().asset_ids() == {"s-1", "c-1"}


def test_a_gone_collider_is_cleared_but_a_gone_splat_drops_the_world():
    doc, changed = scene_with_world().without_assets({"c-1"})
    world = next(o for o in doc.objects if o.kind == "world")
    assert world.collider_asset_id is None and changed == 1

    doc, changed = scene_with_world().without_assets({"s-1"})
    assert all(o.kind != "world" for o in doc.objects) and changed == 1


def test_a_model_plays_a_clip_from_its_own_or_another_glb_and_loses_it_with_that_glb():
    doc = Jutsu3dDocument.model_validate(starter_scene())
    walker = SceneObject(id="mwalker", kind="model", asset_id="rig-1",
                         animation={"clip": "Walking", "source_asset_id": "walk-1", "start": 1.5, "speed": 1.2})
    doc = doc.model_copy(update={"objects": [walker, *doc.objects]})
    assert walker.animation.loop and walker.animation.offset == 0
    assert {"rig-1", "walk-1"} <= doc.asset_ids()

    gone, changed = doc.without_assets({"walk-1"})
    kept = next(o for o in gone.objects if o.id == "mwalker")
    assert kept.animation is None and kept.asset_id == "rig-1" and changed == 1

    with pytest.raises(ValidationError, match="only a model object plays"):
        SceneObject(kind="box", animation={"clip": "Walking"})
    with pytest.raises(ValidationError):
        SceneObject(kind="model", asset_id="rig-1", animation={"clip": "Walking", "speed": 0})


def test_the_studio_room_has_a_default_and_bounds():
    settings = Jutsu3dDocument.model_validate(starter_scene()).settings
    assert settings.environment == 0.4 and settings.ambient == 0.4
    # scenes saved before the setting existed read with the default
    old = starter_scene()
    old["settings"].pop("environment", None)
    assert Jutsu3dDocument.model_validate(old).settings.environment == 0.4
    with pytest.raises(ValidationError):
        Jutsu3dDocument.model_validate({**old, "settings": {**old["settings"], "environment": -0.1}})
    with pytest.raises(ValidationError):
        Jutsu3dDocument.model_validate({**old, "settings": {**old["settings"], "environment": 3.5}})
