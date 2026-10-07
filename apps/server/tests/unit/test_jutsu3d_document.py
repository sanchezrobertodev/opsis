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
