import logging

import numpy as np
import tensorflow as tf
import tensorflow.keras.losses
from marshmallow import Schema, fields
from marshmallow.validate import OneOf, ContainsOnly

from eoflow.base import BaseModel
import tensorflow as tensorflow
from  tensorflow.keras.losses import MeanSquaredError, MeanAbsoluteError, MeanAbsolutePercentageError, Huber
from tensorflow.keras.metrics import MeanSquaredError, MeanAbsoluteError, MeanAbsolutePercentageError
from eoflow.models.metrics import InitializableMetric

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


# Available losses. Add keys with new losses here.
regression_losses = {
    'mse': tensorflow.keras.losses.MeanSquaredError,
    'huber': tensorflow.keras.losses.Huber,
    'mae': tensorflow.keras.losses.MeanAbsoluteError
}

# Available metrics. Add keys with new metrics here.
regression_metrics = {
    'mse': tensorflow.keras.metrics.MeanSquaredError,
    'mape': tensorflow.keras.metrics.MeanAbsolutePercentageError,
    'mae': tensorflow.keras.metrics.MeanAbsoluteError
}


class BaseRegressionModel(BaseModel):
    """ Base for pixel-wise classification models. """

    class _Schema(Schema):
        #n_outputs = fields.Int(required=True, description='Number of output layers', example=1)
        learning_rate = fields.Float(missing=None, description='Learning rate used in training.', example=0.001)
        loss = fields.String(missing='mse', description='Loss function used for training.',
                             validate=OneOf(regression_losses.keys()))
        metrics = fields.List(fields.String, missing=['mse'],
                              description='List of metrics used for evaluation.',
                              validate=ContainsOnly(regression_metrics.keys()))

    def prepare(self, optimizer=None, loss=None, metrics=None, **kwargs):
        """ Prepares the model. Optimizer, loss and metrics are read using the following protocol:
        * If an argument is None, the default value is used from the configuration of the model.
        * If an argument is a key contained in segmentation specific losses/metrics, those are used.
        * Otherwise the argument is passed to `compile` as is.

        """
        # Read defaults if None
        if optimizer is None:
            optimizer = tf.keras.optimizers.Adam(learning_rate=self.config.learning_rate)

        if loss is None:
            loss = self.config.loss

        if metrics is None:
            metrics = self.config.metrics

        loss = regression_losses[loss](**kwargs)

        reported_metrics = []
        for metric in metrics:

            if metric in regression_metrics:
                metric = regression_metrics[metric](**kwargs)

            # Initialize initializable metrics
            if isinstance(metric, InitializableMetric):
                metric.init_from_config(self.config)

            reported_metrics.append(metric)

        self.compile(optimizer=optimizer, loss=loss, metrics=reported_metrics, **kwargs)

    # Override default method to add prediction visualization
    def train(self,
              dataset,
              num_epochs,
              model_directory,
              iterations_per_epoch=None,
              callbacks=[],
              save_steps='epoch',
              summary_steps=1, **kwargs):

        super().train(dataset, num_epochs, model_directory, iterations_per_epoch,
                      callbacks=callbacks, save_steps=save_steps,
                      summary_steps=summary_steps, **kwargs)

    # Override default method to add prediction visualization
    def train_and_evaluate(self,
                           train_dataset,
                           val_dataset,
                           num_epochs,
                           iterations_per_epoch,
                           model_directory,
                           save_steps=100,
                           summary_steps=10,
                           callbacks=[], **kwargs):

        super().train_and_evaluate(train_dataset, val_dataset,
                                   num_epochs, iterations_per_epoch,
                                   model_directory,
                                   save_steps=save_steps, summary_steps=summary_steps,
                                   callbacks=callbacks, **kwargs)