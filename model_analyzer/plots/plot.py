# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
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

import os
import matplotlib.pyplot as plt
from collections import defaultdict
from model_analyzer.record.metrics_manager import MetricsManager


class Plot:
    """
    A wrapper class around a matplotlib
    plot that adapts with the kinds of 
    plots the model analyzer wants to generates

    A singe plot holds data for all 
    model configs, but only holds one
    type of plot
    """

    def __init__(self, name, title, x_axis, y_axis):
        """
        Parameters
        ----------
        name: str
            The name of the file that the plot
            will be saved as 
        title : str
            The title of this plot/figure
        x_axis : str
            The metric tag for the x-axis of this plot
        y_axis : str
            The metric tag for the y-axis of this plot
        """

        self._name = name
        self._title = title
        self._x_axis = x_axis
        self._y_axis = y_axis

        self._fig, self._ax = plt.subplots()

        self._data = {}

        self._ax.set_title(title)

        x_header, y_header = [
            metric.header(aggregation_tag='')
            for metric in MetricsManager.get_metric_types([x_axis, y_axis])
        ]

        self._ax.set_xlabel(x_header)
        self._ax.set_ylabel(y_header)

    def add_measurement(self, model_config_label, measurement):
        """
        Adds a measurment to this plot

        Parameters
        ----------
        model_config_label : str
            The name of the model config this measurement
            is taken from. 
        measurement : Measurement
            The measurement containing the data to
            be plotted.
        """

        if model_config_label not in self._data:
            self._data[model_config_label] = defaultdict(list)

        self._data[model_config_label]['x_data'].append(
            measurement.get_value_of_metric(tag=self._x_axis).value())
        self._data[model_config_label]['y_data'].append(
            measurement.get_value_of_metric(tag=self._y_axis).value())

    def plot_data(self):
        """
        Calls plotting function
        on this plot's Axes object
        """

        for model_config_name, data in self._data.items():
            # Sort the data by x-axis
            x_data, y_data = (
                list(t)
                for t in zip(*sorted(zip(data['x_data'], data['y_data']))))
            self._ax.plot(x_data, y_data, marker='o', label=model_config_name)
        self._ax.legend()
        self._ax.grid()

    def data(self):
        """
        Get the data in this plot
        
        Returns
        -------
        dict
            keys are line labels
            and values are lists of floats
        """

        return self._data

    def save(self, filepath):
        """
        Saves a .png of the plot to disk

        Parameters
        ----------
        filepath : the path to the directory
            this plot should be saved to
        """

        self._fig.savefig(os.path.join(filepath, self._name))