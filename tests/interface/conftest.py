"""Conftest for interface tests — mock heavy dependencies not available in test env."""

import sys
import types
from unittest.mock import MagicMock


def _create_mock_module(name: str) -> MagicMock:
    """Create a MagicMock that behaves as a module."""
    mock = MagicMock(spec=types.ModuleType)
    mock.__name__ = name
    mock.__path__ = []
    mock.__package__ = name
    return mock


# ---- litellm mock tree ----
_litellm = _create_mock_module("litellm")
_litellm.drop_params = True
_litellm.modify_params = True
_litellm.model_cost = {}
_litellm._should_retry = MagicMock(return_value=False)
_litellm.completion = MagicMock()
_litellm.acompletion = MagicMock()
_litellm.stream_chunk_builder = MagicMock()
_litellm.supports_reasoning = MagicMock(return_value=False)
_litellm.token_counter = MagicMock(return_value=0)

_litellm_logging = _create_mock_module("litellm._logging")
_litellm_logging._disable_debugging = MagicMock()
_litellm._logging = _litellm_logging

_litellm_utils = _create_mock_module("litellm.utils")
_litellm_utils.supports_prompt_caching = MagicMock(return_value=False)
_litellm_utils.supports_vision = MagicMock(return_value=False)
_litellm.utils = _litellm_utils

_litellm_proxy = _create_mock_module("litellm.proxy")
_litellm.proxy = _litellm_proxy

# ---- docker mock tree ----
_docker = _create_mock_module("docker")
_docker.from_env = MagicMock()

_docker_errors = _create_mock_module("docker.errors")
_docker_errors.DockerException = type("DockerException", (Exception,), {})
_docker_errors.ImageNotFound = type("ImageNotFound", (Exception,), {})
_docker_errors.NotFound = type("NotFound", (Exception,), {})
_docker.errors = _docker_errors

_docker_models = _create_mock_module("docker.models")
_docker_models_containers = _create_mock_module("docker.models.containers")
_docker_models_containers.Container = MagicMock
_docker_models.containers = _docker_models_containers
_docker.models = _docker_models

_docker_types = _create_mock_module("docker.types")
_docker.types = _docker_types

# ---- textual_image mock tree ----
_textual_image = _create_mock_module("textual_image")
_textual_image_widget = _create_mock_module("textual_image.widget")
_textual_image_widget.Image = MagicMock
_textual_image.widget = _textual_image_widget

# ---- Register all mocks ----
_all_mocks = {
    "litellm": _litellm,
    "litellm._logging": _litellm_logging,
    "litellm.utils": _litellm_utils,
    "litellm.proxy": _litellm_proxy,
    "docker": _docker,
    "docker.errors": _docker_errors,
    "docker.models": _docker_models,
    "docker.models.containers": _docker_models_containers,
    "docker.types": _docker_types,
    "textual_image": _textual_image,
    "textual_image.widget": _textual_image_widget,
}

for mod_name, mock_mod in _all_mocks.items():
    if mod_name not in sys.modules:
        sys.modules[mod_name] = mock_mod
