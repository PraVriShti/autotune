import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.shortcuts import get_object_or_404
from pydantic import BaseModel

from workflow.models import WorkflowConfig


def get_workflow_config(workflow_config):
    """
    Fetches a WorkflowConfig object from the cache or database by workflow_config.

    :param workflow_config: The type of the workflow to fetch the config for.
    :return: WorkflowConfig instance
    raises: HTTPError: Http 404 if no workflow config found in db.
    """
    cache_key = f"workflow_config_{workflow_config}"
    config = cache.get(cache_key)

    if config is None:
        get_object_or_404(WorkflowConfig, id=workflow_config)

    return config


def dehydrate_cache(key_pattern):
    """
    Dehydrates (clears) cache entries based on a given key pattern.
    This function can be used to invalidate specific cache entries manually,
    especially after database updates, to ensure cache consistency.

    Parameters:
    - key_pattern (str): The cache key pattern to clear. This can be a specific cache key
      or a pattern representing a group of keys.

    Returns:
    - None
    """
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern(key_pattern)
    else:
        cache.delete(key_pattern)


def create_pydantic_model(jsonData):
    Model = None
    with tempfile.NamedTemporaryFile(
        mode="w+", dir=os.getcwd(), suffix=".json", delete=True
    ) as tmp_json:
        json.dump(jsonData, tmp_json)
        tmp_json.flush()

        with tempfile.NamedTemporaryFile(
            mode="w+", dir=os.getcwd(), suffix=".py", delete=True
        ) as tmp_py:
            command = (
                f"datamodel-codegen --input {tmp_json.name} --output {tmp_py.name}"
            )

            try:
                subprocess.run(command, check=True, shell=True)
                print("model generation successful")
            except subprocess.CalledProcessError as e:
                print("An error occurred while generating the pydantic model:", e)

            tmp_py_path = Path(tmp_py.name)
            Model = import_model_from_generated_file(tmp_py_path)

            class_string = get_classes_from_module(tmp_py_path, base_class=BaseModel)

    return Model, class_string


def import_model_from_generated_file(file_path):
    directory, module_name = os.path.split(file_path)
    module_name = os.path.splitext(module_name)[0]

    if directory not in sys.path:
        sys.path.append(directory)

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    Model = getattr(module, "Model", None)
    return Model


def import_module_from_path(path):
    """Import a module from the given file path"""
    if isinstance(path, str):
        path = Path(path)  # Convert string to Path if necessary
    module_name = path.stem  # Using path.stem to get a module name from the file name
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_classes_from_module(path, base_class):
    module = import_module_from_path(path)
    class_details = ""
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, base_class) and obj.__module__ == module.__name__:
            class_details += f"\nclass {name}({base_class.__name__}):\n"
            for field_name, field_type in obj.__annotations__.items():
                field_type_name = (
                    field_type.__name__
                    if hasattr(field_type, "__name__")
                    else repr(field_type)
                )
                class_details += f"  {field_name}: {field_type_name}\n"

    return class_details
