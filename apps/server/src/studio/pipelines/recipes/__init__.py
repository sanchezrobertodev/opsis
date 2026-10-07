"""Built-in recipes, by id."""

from __future__ import annotations

from studio.pipelines.recipe import Recipe
from studio.pipelines.recipes.cinema import CinemaExportRecipe, CinemaFramesRecipe, CinemaTakesRecipe
from studio.pipelines.recipes.compare import CompareRecipe
from studio.pipelines.recipes.faceless import FacelessRecipe
from studio.pipelines.recipes.keyframe import KeyframeRecipe
from studio.pipelines.recipes.object_swap import ObjectSwapRecipe
from studio.pipelines.recipes.sequence import SequenceRecipe

RECIPES: dict[str, Recipe] = {r.id: r for r in (
    SequenceRecipe(), CompareRecipe(), KeyframeRecipe(), FacelessRecipe(), ObjectSwapRecipe(),
    CinemaFramesRecipe(), CinemaTakesRecipe(), CinemaExportRecipe(),
)}


def get_recipe(recipe_id: str) -> Recipe | None:
    return RECIPES.get(recipe_id)
