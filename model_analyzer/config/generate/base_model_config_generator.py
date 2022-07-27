# Copyright (c) 2022, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from .config_generator_interface import ConfigGeneratorInterface

from model_analyzer.constants import LOGGER_NAME
from model_analyzer.triton.model.model_config import ModelConfig

import abc
import logging

logger = logging.getLogger(LOGGER_NAME)


class BaseModelConfigGenerator(ConfigGeneratorInterface):
    """ Base class for generating model configs """

    def __init__(self, config, gpus, model, client, variant_name_manager,
                 default_only, early_exit_enable):
        """
        Parameters
        ----------
        config: ModelAnalyzerConfig
        gpus: List of GPUDevices
        model: The model to generate ModelConfigs for
        client: TritonClient
        variant_name_manager: ModelVariantNameManager
        default_only: Bool
            If true, only the default config will be generated
            If false, the default config will NOT be generated
        early_exit_enable: Bool
            If true, the generator can early exit if throughput plateaus
        """
        self._config = config
        self._gpus = gpus
        self._client = client
        self._variant_name_manager = variant_name_manager
        self._model_repository = config.model_repository
        self._base_model = model
        self._base_model_name = model.model_name()
        self._remote_mode = config.triton_launch_mode == 'remote'
        self._cpu_only = model.cpu_only()
        self._default_only = default_only
        self._early_exit_enable = early_exit_enable
        self._model_name_index = 0
        self._generator_started = False
        self._max_batch_size_warning_printed = False
        self._last_results = []
        # Contains the max throughput from each provided list of measurements
        # since the last time we stepped max_batch_size
        #
        self._curr_max_batch_size_throughputs = []

    def _is_done(self):
        """ Returns true if this generator is done generating configs """
        return self._generator_started and (self._default_only or
                                            self._done_walking())

    def get_configs(self):
        """
        Returns
        -------
        ModelConfig
            The next ModelConfig generated by this class
        """
        while True:
            if self._is_done():
                break

            self._generator_started = True
            config = self._get_next_model_config()
            yield (config)
            self._step()

    def set_last_results(self, measurements):
        """
        Given the results from the last ModelConfig, make decisions
        about future configurations to generate

        Parameters
        ----------
        measurements: List of Measurements from the last run(s)
        """
        self._last_results = measurements

    @abc.abstractmethod
    def _done_walking(self):
        raise NotImplementedError

    @abc.abstractmethod
    def _step(self):
        raise NotImplementedError

    @abc.abstractmethod
    def _get_next_model_config(self):
        raise NotImplementedError

    def _last_results_erroneous(self):
        last_max_throughput = self._get_last_results_max_throughput()
        return last_max_throughput is None

    def _last_results_increased_throughput(self):
        if len(self._curr_max_batch_size_throughputs) < 2:
            return True

        lastest_throughput = self._curr_max_batch_size_throughputs[-1]
        return all(
            lastest_throughput > prev_throughput
            for prev_throughput in self._curr_max_batch_size_throughputs[:-1])

    def _get_last_results_max_throughput(self):
        throughputs = [
            m.get_non_gpu_metric_value('perf_throughput')
            for m in self._last_results
            if m is not None
        ]
        if not throughputs:
            return None
        else:
            return max(throughputs)

    def _make_remote_model_config(self):
        if not self._reload_model_disable:
            self._client.load_model(self._base_model_name)
        model_config = ModelConfig.create_from_triton_api(
            self._client, self._base_model_name, self._num_retries)
        model_config.set_cpu_only(self._cpu_only)
        if not self._reload_model_disable:
            self._client.unload_model(self._base_model_name)

        return model_config

    def _make_direct_mode_model_config(self, param_combo):
        return BaseModelConfigGenerator.make_model_config(
            param_combo=param_combo,
            config=self._config,
            client=self._client,
            gpus=self._gpus,
            model=self._base_model,
            model_repository=self._model_repository,
            variant_name_manager=self._variant_name_manager)

    @staticmethod
    def make_model_config(param_combo, config, client, gpus, model,
                          model_repository, variant_name_manager):
        """
        Loads the base model config from the model repository, and then applies the
        parameters in the param_combo on top to create and return a new model config

        Parameters:
        -----------
        param_combo: dict
            dict of key:value pairs to apply to the model config
        config: ModelAnalyzerConfig
        client: TritonClient
        gpus: List of GPUDevices
        model: dict
            dict of model properties
        model_repository: str
            path to the model repository on the file system
        variant_name_manager: ModelVariantNameManager
        """
        model_name = model.model_name()
        variant_name = variant_name_manager.get_model_variant_name(
            model_name, param_combo)

        model_config_dict = BaseModelConfigGenerator.get_base_model_config_dict(
            config, client, gpus, model_repository, model_name)

        model_config_dict['name'] = variant_name
        logger.info("")
        logger.info(f"Creating model config: {model_config_dict['name']}")

        if param_combo is not None:
            for key, value in param_combo.items():
                if value is not None:
                    BaseModelConfigGenerator._apply_value_to_dict(
                        key, value, model_config_dict)

                    if value == {}:
                        logger.info(f"  Enabling {key}")
                    else:
                        logger.info(f"  Setting {key} to {value}")
        logger.info("")

        model_config = ModelConfig.create_from_dictionary(model_config_dict)
        model_config.set_cpu_only(model.cpu_only())

        return model_config

    @classmethod
    def get_base_model_config_dict(cls, config, client, gpus, model_repository,
                                   model_name):
        """
        Attempts to create a base model config dict from config.pbtxt, if one exists
        If the config.pbtxt is not present, we will load a Triton Server with the
        base model and have it create a default config for MA, if possible

        Parameters:
        -----------
        config: ModelAnalyzerConfig
        client: TritonClient
        gpus: List of GPUDevices
        model_repository: str
            path to the model repository on the file system
        model_name: str
            name of the base model
        """
        model_config_dict = ModelConfig.create_model_config_dict(
            config, client, gpus, model_repository, model_name)

        return model_config_dict

    def _reset_max_batch_size(self):
        self._max_batch_size_warning_printed = False
        self._curr_max_batch_size_throughputs = []

    def _print_max_batch_size_plateau_warning(self):
        if not self._max_batch_size_warning_printed:
            logger.info(
                "No longer increasing max_batch_size because throughput has plateaued"
            )
            self._max_batch_size_warning_printed = True
        return True

    @staticmethod
    def _apply_value_to_dict(key, value, dict_in):
        """
        Apply the supplied value at the given key into the provided dict.

        If the key already exists in the dict and both the existing value as well
        as the new input value are dicts, only overwrite the subkeys (recursively)
        provided in the value
        """

        if type(dict_in.get(key, None)) is dict and type(value) is dict:
            for subkey, subvalue in value.items():
                BaseModelConfigGenerator._apply_value_to_dict(
                    subkey, subvalue, dict_in.get(key, None))
        else:
            dict_in[key] = value
