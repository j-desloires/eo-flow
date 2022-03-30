import pandas as pd

import eoflow.models.tempnets_task.cnn_tempnets as cnn_tempnets
import eoflow.models.tempnets_task.cnn_tempnets_functional  as cnn_tempnets_functional
import tensorflow as tf

# Model configuration CNNLSTM
import numpy as np
import os
import tensorflow_addons as tfa
import matplotlib.pyplot as plt
from eoflow.models.data_augmentation import feature_noise, timeshift, noisy_label
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestRegressor


########################################################################################################################
########################################################################################################################

def reshape_array(x, T=30):
    x = x.reshape(x.shape[0], x.shape[1] // T, T)
    x = np.moveaxis(x, 2, 1)
    return x


def npy_concatenate(path, prefix='training_x', T=30):
    path_npy = os.path.join(path, prefix)

    x = np.load(path_npy + '_S2.npy')
    x = reshape_array(x, T)
    return x


path = '/home/johann/Documents/Syngenta/cleaned_V2/2021'

x_train = npy_concatenate(path, 'training_x')
y_train = np.load(os.path.join(path, 'training_y.npy'))

x_val = npy_concatenate(path, 'val_x')
y_val = np.load(os.path.join(path, 'val_y.npy'))

x_test = npy_concatenate(path, 'test_x')
y_test = np.load(os.path.join(path, 'test_y.npy'))


'''
from sklearn.ensemble import RandomForestRegressor
model = RandomForestRegressor(max_depth=8)
x_train = x_train.reshape((x_train.shape[0],x_train.shape[1]*x_train.shape[2]))
x_test = x_test.reshape((x_test.shape[0],x_test.shape[1]*x_test.shape[2]))
model.fit(x_train, y_train)
preds = model.predict(x_test)
r2_score(y_test, preds)

'''

model_cfg_cnn_stride = {
    "learning_rate": 10e-4,
    "keep_prob": 0.65,  # should keep 0.8
    "nb_conv_filters": 32,  # wiorks great with 32
    "nb_fc_neurons": 32,
    "metrics": "r_square",
    "loss": "mse",
    'factor' : 0.05
}


model_cnn = cnn_tempnets_functional.TempDANN(model_cfg_cnn_stride)
# Prepare the model (must be run before training)
model_cnn.prepare()
self = model_cnn
x = x_train
self(x)
self.summary()

model_cnn.fit_dann_v3(
    src_dataset=(x_train, y_train),
    val_dataset=(x_test, y_test),
    trgt_dataset=(x_test, y_test),
    num_epochs=500,
    save_steps=5,
    batch_size=12,
    patience=50,
    fillgaps=0,
    shift_step=0,
    sdev_label=0,
    feat_noise=0,
    reduce_lr=True,
    model_directory='/home/johann/Documents/model_16',
)


#Total params: 28,570
y, yt = model_cnn.predict(x_test)
plt.scatter(y_test, y)
plt.show()

r2_score(y_test, y)
mean_absolute_error(y_test, y)
model_cnn.summary()