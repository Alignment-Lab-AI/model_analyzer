#!/usr/bin/env python3

# Copyright 2022-2023, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Generator, List, Optional

from model_analyzer.config.generate.model_variant_name_manager import (
    ModelVariantNameManager,
)
from model_analyzer.config.input.config_command_profile import ConfigCommandProfile
from model_analyzer.config.run.model_run_config import ModelRunConfig
from model_analyzer.device.gpu_device import GPUDevice
from model_analyzer.perf_analyzer.perf_config import PerfAnalyzerConfig
from model_analyzer.result.run_config_measurement import RunConfigMeasurement
from model_analyzer.triton.client.client import TritonClient
from model_analyzer.triton.model.model_config_variant import ModelConfigVariant

from .config_generator_interface import ConfigGeneratorInterface
from .model_config_generator_factory import ModelConfigGeneratorFactory
from .model_profile_spec import ModelProfileSpec
from .perf_analyzer_config_generator import PerfAnalyzerConfigGenerator


class ModelRunConfigGenerator(ConfigGeneratorInterface):
    """
    Given a model, generates all ModelRunConfigs (combination of
    ModelConfig and PerfConfig)
    """

    def __init__(
        self,
        config: ConfigCommandProfile,
        gpus: List[GPUDevice],
        model: ModelProfileSpec,
        client: TritonClient,
        model_variant_name_manager: ModelVariantNameManager,
        default_only: bool,
    ) -> None:
        """
        Parameters
        ----------
        config: ModelAnalyzerConfig

        gpus: List of GPUDevices

        model: ConfigModelProfileSpec
            The model to generate ModelRunConfigs for

        client: TritonClient

        model_variant_name_manager: ModelVariantNameManager

        default_only: Bool
        """
        self._config = config
        self._gpus = gpus
        self._model = model
        self._client = client
        self._model_variant_name_manager = model_variant_name_manager

        self._model_name = model.model_name()

        self._model_pa_flags = model.perf_analyzer_flags()
        self._model_parameters = model.parameters()
        self._triton_server_env = model.triton_server_environment()

        self._determine_early_exit_enables(config, model)

        self._mcg = ModelConfigGeneratorFactory.create_model_config_generator(
            self._config,
            self._gpus,
            model,
            self._client,
            self._model_variant_name_manager,
            default_only,
            self._mcg_early_exit_enable,
        )

        self._curr_mc_measurements: List[Optional[RunConfigMeasurement]] = []

    def get_configs(self) -> Generator[ModelRunConfig, None, None]:
        """
        Returns
        -------
        ModelRunConfig
            The next ModelRunConfig generated by this class
        """
        for model_config_variant in self._mcg.get_configs():
            self._pacg = PerfAnalyzerConfigGenerator(
                self._config,
                model_config_variant.variant_name,
                self._model_pa_flags,
                self._model_parameters,
                self._pacg_early_exit_enable,
            )

            for perf_analyzer_config in self._pacg.get_configs():
                run_config = self._generate_model_run_config(
                    model_config_variant, perf_analyzer_config
                )
                yield run_config

            self._set_last_results_model_config_generator()

    def set_last_results(
        self, measurements: List[Optional[RunConfigMeasurement]]
    ) -> None:
        """
        Given the results from the last ModelRunConfig, make decisions
        about future configurations to generate

        Parameters
        ----------
        measurements: List of Measurements from the last run(s)
        """
        self._pacg.set_last_results(measurements)
        self._curr_mc_measurements.extend(measurements)

    def _set_last_results_model_config_generator(self) -> None:
        self._mcg.set_last_results(self._curr_mc_measurements)
        self._curr_mc_measurements = []

    def _generate_model_run_config(
        self,
        model_config_variant: ModelConfigVariant,
        perf_analyzer_config: PerfAnalyzerConfig,
    ) -> ModelRunConfig:
        run_config = ModelRunConfig(
            self._model_name, model_config_variant, perf_analyzer_config
        )

        return run_config

    def _determine_early_exit_enables(
        self, config: ConfigCommandProfile, model: ModelProfileSpec
    ) -> None:
        early_exit_enable = config.early_exit_enable
        concurrency_specified = model.parameters()["concurrency"]
        config_parameters_exist = model.model_config_parameters()

        self._pacg_early_exit_enable = early_exit_enable or not concurrency_specified
        self._mcg_early_exit_enable = early_exit_enable or not config_parameters_exist
