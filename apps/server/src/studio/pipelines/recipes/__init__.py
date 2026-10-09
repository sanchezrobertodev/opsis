"""Built-in recipes, by id."""

from __future__ import annotations

from studio.pipelines.recipe import Recipe
from studio.pipelines.recipes.cinema import (
    CinemaEditRecipe,
    CinemaExportRecipe,
    CinemaExtendRecipe,
    CinemaFramesRecipe,
    CinemaSceneRecipe,
    CinemaTakesRecipe,
)
from studio.pipelines.recipes.compare import CompareRecipe
from studio.pipelines.recipes.faceless import FacelessRecipe
from studio.pipelines.recipes.jutsu3d import Jutsu3dRecipe
from studio.pipelines.recipes.keyframe import KeyframeRecipe
from studio.pipelines.recipes.lora_train import LoraTrainRecipe
from studio.pipelines.recipes.object_swap import ObjectSwapRecipe
from studio.pipelines.recipes.sequence import SequenceRecipe
from studio.pipelines.recipes.talking import TalkingShotRecipe

RECIPES: dict[str, Recipe] = {r.id: r for r in (
    SequenceRecipe(), CompareRecipe(), KeyframeRecipe(), FacelessRecipe(), ObjectSwapRecipe(), TalkingShotRecipe(),
    CinemaFramesRecipe(), CinemaTakesRecipe(), CinemaSceneRecipe(), CinemaExtendRecipe(), CinemaEditRecipe(),
    CinemaExportRecipe(), Jutsu3dRecipe(), LoraTrainRecipe(),
)}


def get_recipe(recipe_id: str) -> Recipe | None:
    return RECIPES.get(recipe_id)
