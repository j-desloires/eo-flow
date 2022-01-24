import logging
import tensorflow as tf
from marshmallow import fields
from marshmallow.validate import OneOf

from keras.layers import TimeDistributed
from tensorflow.keras.layers import SimpleRNN, LSTM, GRU, Dense
from tensorflow.python.keras.utils.layer_utils import print_summary

from eoflow.models.layers import ResidualBlock
from eoflow.models.regression_task.regression_base import BaseRegressionModel

from eoflow.models import transformer_encoder_layers
from eoflow.models import pse_tae_layers

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

rnn_layers = dict(rnn=SimpleRNN, gru=GRU, lstm=LSTM)


class TCNModel(BaseRegressionModel):
    """ Implementation of the TCN network taken form the keras-TCN implementation

        https://github.com/philipperemy/keras-tcn
    """

    class TCNModelSchema(BaseRegressionModel._Schema):
        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)

        kernel_size = fields.Int(missing=2, description='Size of the convolution kernels.')
        nb_filters = fields.Int(missing=64, description='Number of convolutional filters.')
        nb_conv_stacks = fields.Int(missing=1)
        dilations = fields.List(fields.Int, missing=[1, 2, 4, 8, 16, 32], description='Size of dilations used in the '
                                                                                      'covolutional layers')
        padding = fields.String(missing='CAUSAL', validate=OneOf(['CAUSAL', 'SAME']),
                                description='Padding type used in convolutions.')
        use_skip_connections = fields.Bool(missing=True, description='Flag to whether to use skip connections.')
        return_sequences = fields.Bool(missing=False, description='Flag to whether return sequences or not.')
        activation = fields.Str(missing='linear', description='Activation function used in final filters.')
        kernel_initializer = fields.Str(missing='he_normal', description='method to initialise kernel parameters.')
        kernel_regularizer = fields.Float(missing=0, description='L2 regularization parameter.')

        batch_norm = fields.Bool(missing=False, description='Whether to use batch normalisation.')
        layer_norm = fields.Bool(missing=False, description='Whether to use layer normalisation.')

    def _cnn_layer(self, net):

        dropout_rate = 1 - self.config.keep_prob

        layer = tf.keras.layers.Conv1D(filters= self.config.nb_filters,
                                       kernel_size=self.config.kernel_size,
                                       padding=self.config.padding,
                                       kernel_initializer=self.config.kernel_initializer,
                                       kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))(net)
        if self.config.batch_norm:
            layer = tf.keras.layers.BatchNormalization(axis=-1)(layer)

        layer = tf.keras.layers.Dropout(dropout_rate)(layer)
        layer = tf.keras.layers.Activation(self.config.activation)(layer)
        return layer

    def build(self, inputs_shape):
        """ Build TCN architecture

        The `inputs_shape` argument is a `(N, T, D)` tuple where `N` denotes the number of samples, `T` the number of
        time-frames, and `D` the number of channels
        """
        x = tf.keras.layers.Input(inputs_shape[1:])

        dropout_rate = 1 - self.config.keep_prob

        net = x

        net = self._cnn_layer(net)

        # list to hold all the member ResidualBlocks
        residual_blocks = list()
        skip_connections = list()

        total_num_blocks = self.config.nb_conv_stacks * len(self.config.dilations)
        if not self.config.use_skip_connections:
            total_num_blocks += 1  # cheap way to do a false case for below

        for _ in range(self.config.nb_conv_stacks):
            for d in self.config.dilations:
                net, skip_out = ResidualBlock(dilation_rate=d,
                                              nb_filters=self.config.nb_filters,
                                              kernel_size=self.config.kernel_size,
                                              padding=self.config.padding,
                                              activation=self.config.activation,
                                              dropout_rate=dropout_rate,
                                              use_batch_norm=self.config.batch_norm,
                                              use_layer_norm=self.config.layer_norm,
                                              kernel_initializer=self.config.kernel_initializer,
                                              last_block=len(residual_blocks) + 1 == total_num_blocks,
                                              name=f'residual_block_{len(residual_blocks)}')(net)
                residual_blocks.append(net)
                skip_connections.append(skip_out)


        # Author: @karolbadowski.
        output_slice_index = int(net.shape.as_list()[1] / 2) \
            if self.config.padding.lower() == 'same' else -1
        lambda_layer = tf.keras.layers.Lambda(lambda tt: tt[:, output_slice_index, :])

        if self.config.use_skip_connections:
            net = tf.keras.layers.add(skip_connections)

        if not self.config.return_sequences:
            net = lambda_layer(net)

        net = tf.keras.layers.Dense(1, activation='linear')(net)

        self.net = tf.keras.Model(inputs=x, outputs=net)

        print_summary(self.net)

    def call(self, inputs, training=None):
        return self.net(inputs, training)


class MLP(BaseRegressionModel):
    """ Implementation of the mlp network

    """

    class MLPSchema(BaseRegressionModel._Schema):
        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)
        nb_fc_neurons = fields.Int(missing=256, description='Number of Fully Connect neurons.')
        nb_fc_stacks = fields.Int(missing=1, description='Number of fully connected layers.')
        activation = fields.Str(missing='relu', description='Activation function used in final filters.')
        kernel_initializer = fields.Str(missing='he_normal', description='Method to initialise kernel parameters.')
        kernel_regularizer = fields.Float(missing=1e-6, description='L2 regularization parameter.')
        batch_norm = fields.Bool(missing=False, description='Whether to use batch normalisation.')

    def build(self, inputs_shape):
        """ Build TCN architecture

        The `inputs_shape` argument is a `(N, T*D)` tuple where `N` denotes the number of samples, `T` the number of
        time-frames, and `D` the number of channels
        """
        x = tf.keras.layers.Input(inputs_shape[1:])
        net = x

        dropout_rate = 1 - self.config.keep_prob

        for _ in range(self.config.nb_fc_stacks):
            net = Dense(units=self.config.nb_fc_neurons,
                                        kernel_initializer=self.config.kernel_initializer,
                                        kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))(net)
            if self.config.batch_norm:
                net = tf.keras.layers.BatchNormalization(axis=-1)(net)

            net = tf.keras.layers.Dropout(dropout_rate)(net)
            net = tf.keras.layers.Activation(self.config.activation)(net)

        net = tf.keras.layers.Dense(units = 1,
                                    activation = 'linear',
                                    kernel_initializer=self.config.kernel_initializer,
                                    kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))(net)

        self.net = tf.keras.Model(inputs=x, outputs=net)

        print_summary(self.net)

    def call(self, inputs, training=None):
        return self.net(inputs, training)


class TempCNNModel(BaseRegressionModel):
    """ Implementation of the TempCNN network taken from the temporalCNN implementation

        https://github.com/charlotte-pel/temporalCNN
    """

    class TempCNNModelSchema(BaseRegressionModel._Schema):
        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)
        kernel_size = fields.Int(missing=5, description='Size of the convolution kernels.')
        nb_conv_filters = fields.Int(missing=16, description='Number of convolutional filters.')
        nb_conv_stacks = fields.Int(missing=3, description='Number of convolutional blocks.')
        nb_conv_strides = fields.Int(missing=1, description='Value of convolutional strides.')
        nb_fc_neurons = fields.Int(missing=256, description='Number of Fully Connect neurons.')
        nb_fc_stacks = fields.Int(missing=1, description='Number of fully connected layers.')
        final_layer = fields.String(missing='Flatten', validate=OneOf(['Flatten','GlobalAveragePooling1D', 'GlobalMaxPooling1D']),
                                    description='Final layer after the convolutions.')
        padding = fields.String(missing='SAME', validate=OneOf(['SAME','VALID', 'CAUSAL']),
                                description='Padding type used in convolutions.')
        pooling = fields.String(missing='SAME', validate=OneOf(['SAME','VALID', 'CAUSAL']),
                                  description='Padding type used in convolutions.')
        activation = fields.Str(missing='relu', description='Activation function used in final filters.')
        kernel_initializer = fields.Str(missing='he_normal', description='Method to initialise kernel parameters.')
        kernel_regularizer = fields.Float(missing=1e-6, description='L2 regularization parameter.')
        enumerate = fields.Bool(missing=False, description='Increase number of filters across convolution')
        batch_norm = fields.Bool(missing=False, description='Whether to use batch normalisation.')

    def _cnn_layer(self, net, i = 0):

        dropout_rate = 1 - self.config.keep_prob
        filters = self.config.nb_conv_filters
        kernel_size = self.config.kernel_size

        if self.config.enumerate:
            filters = filters * (2**i)
            kernel_size = kernel_size * (i+1)

        layer = tf.keras.layers.Conv1D(filters=filters,
                                       kernel_size=kernel_size,
                                       strides=self.config.nb_conv_strides,
                                       padding=self.config.padding,
                                       kernel_initializer=self.config.kernel_initializer,
                                       kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))(net)
        if self.config.batch_norm:
            layer = tf.keras.layers.BatchNormalization(axis=-1)(layer)

        #if self.config.enumerate: layer = tf.keras.layers.MaxPool1D()(layer)

        layer = tf.keras.layers.Dropout(dropout_rate)(layer)
        layer = tf.keras.layers.Activation(self.config.activation)(layer)
        return layer

    def _embeddings(self,net):

        name = "embedding"
        if self.config.final_layer == 'Flatten':
            net = tf.keras.layers.Flatten(name=name)(net)
        elif self.config.final_layer == 'GlobalAveragePooling1D':
            net = tf.keras.layers.GlobalAveragePooling1D(name=name)(net)
        elif self.config.final_layer == 'GlobalMaxPooling1D':
            net = tf.keras.layers.GlobalMaxPooling1D(name=name)(net)

        return net

    def _fcn_layer(self, net):
        dropout_rate = 1 - self.config.keep_prob
        layer_fcn = Dense(units=self.config.nb_fc_neurons,
                          kernel_initializer=self.config.kernel_initializer,
                          kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))(net)
        if self.config.batch_norm:
            layer_fcn = tf.keras.layers.BatchNormalization(axis=-1)(layer_fcn)

        layer_fcn = tf.keras.layers.Dropout(dropout_rate)(layer_fcn)

        return layer_fcn


    def build(self, inputs_shape):
        """ Build TCN architecture

        The `inputs_shape` argument is a `(N, T, D)` tuple where `N` denotes the number of samples, `T` the number of
        time-frames, and `D` the number of channels
        """
        x = tf.keras.layers.Input(inputs_shape[1:])

        net = x
        for i, _ in enumerate(range(self.config.nb_conv_stacks)):
            net = self._cnn_layer(net, i)

        embeddings = self._embeddings(net)

        for _ in range(self.config.nb_fc_stacks):
            net = self._fcn_layer(embeddings)

        net = Dense(units = 1,
                    activation = 'linear',
                    kernel_initializer=self.config.kernel_initializer,
                    kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))(net)

        self.net = tf.keras.Model(inputs=x, outputs=net)
        self.backbone = tf.keras.Model(inputs = x, outputs = embeddings)

        print_summary(self.net)

    def call(self, inputs, training=None):
        return self.net(inputs, training)

    def get_feature_map(self, inputs, training=None):
        return self.backbone(inputs, training)




class BiRNN(BaseRegressionModel):
    """ Implementation of a Bidirectional Recurrent Neural Network

    This implementation allows users to define which RNN layer to use, e.g. SimpleRNN, GRU or LSTM
    """

    class BiRNNModelSchema(BaseRegressionModel._Schema):
        rnn_layer = fields.String(required=True, validate=OneOf(['rnn', 'lstm', 'gru']),
                                  description='Type of RNN layer to use')

        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)

        rnn_units = fields.Int(missing=64, description='Size of the convolution kernels.')
        rnn_blocks = fields.Int(missing=1, description='Number of LSTM blocks')
        bidirectional = fields.Bool(missing=True, description='Whether to use a bidirectional layer')

        activation = fields.Str(missing='relu', description='Activation function for fully connected layers')
        kernel_initializer = fields.Str(missing='he_normal', description='Method to initialise kernel parameters.')
        kernel_regularizer = fields.Float(missing=1e-6, description='L2 regularization parameter.')
        nb_fc_stacks = fields.Int(missing=0, description='Number of fully connected layers.')
        nb_fc_neurons = fields.Int(missing=0, description='Number of fully connected neurons.')

        layer_norm = fields.Bool(missing=True, description='Whether to apply layer normalization in the encoder.')
        batch_norm = fields.Bool(missing=False, description='Whether to use batch normalisation.')

    def _rnn_layer(self, last=False):
        """ Returns a RNN layer for current configuration. Use `last=True` for the last RNN layer. """
        RNNLayer = rnn_layers[self.config.rnn_layer]
        dropout_rate = 1 - self.config.keep_prob

        layer = RNNLayer(
            units=self.config.rnn_units,
            dropout=dropout_rate,
            return_sequences=not last,
        )

        # Use bidirectional if specified
        if self.config.bidirectional:
            layer = tf.keras.layers.Bidirectional(layer)

        return layer

    def _fcn_layer(self):
        dropout_rate = 1 - self.config.keep_prob
        layer_fcn = Dense(units=self.config.nb_fc_neurons,
                          kernel_initializer=self.config.kernel_initializer,
                          kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))
        if self.config.batch_norm:
            layer_fcn = tf.keras.layers.BatchNormalization(axis=-1)(layer_fcn)

        layer_fcn = tf.keras.layers.Dropout(dropout_rate)(layer_fcn)

        return layer_fcn

    def init_model(self):
        """ Creates the RNN model architecture. """

        layers = []
        if self.config.layer_norm:
            layer_norm = tf.keras.layers.LayerNormalization()
            layers.append(layer_norm)

        # RNN layers
        layers.extend([self._rnn_layer() for _ in range(self.config.rnn_blocks-1)])
        layers.append(self._rnn_layer(last=True))

        if self.config.batch_norm:
            batch_norm = tf.keras.layers.BatchNormalization()
            layers.append(batch_norm)

        if self.config.layer_norm:
            layer_norm = tf.keras.layers.LayerNormalization()
            layers.append(layer_norm)

        layers.extend([self._fcn_layer() for _ in range(self.config.nb_fc_stacks)])

        dense = tf.keras.layers.Dense(units=1,
                                      activation='linear',
                                      kernel_initializer=self.config.kernel_initializer,
                                      kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))

        layers.append(dense)

        self.net = tf.keras.Sequential(layers)

    def build(self, inputs_shape):
        self.net.build(inputs_shape)

        print_summary(self.net)

    def call(self, inputs, training=None):
        return self.net(inputs, training)



#https://www.sciencedirect.com/science/article/pii/S0034425721003205


class ConvLSTM(BaseRegressionModel):
    """ Implementation of a Bidirectional Recurrent Neural Network

    This implementation allows users to define which RNN layer to use, e.g. SimpleRNN, GRU or LSTM
    """


    class ConvLSTMShema(BaseRegressionModel._Schema):
        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)
        kernel_size = fields.Int(missing=5, description='Size of the convolution kernels.')
        nb_conv_filters = fields.Int(missing=16, description='Number of convolutional filters.')
        nb_conv_stacks = fields.Int(missing=3, description='Number of convolutional blocks.')
        nb_conv_strides = fields.Int(missing=1, description='Value of convolutional strides.')
        nb_fc_neurons = fields.Int(missing=256, description='Number of Fully Connect neurons.')
        nb_fc_stacks = fields.Int(missing=1, description='Number of fully connected layers.')

        final_layer = fields.String(missing='Flatten', validate=OneOf(['Flatten','GlobalAveragePooling1D', 'GlobalMaxPooling1D']),
                                    description='Final layer after the convolutions.')
        padding = fields.String(missing='SAME', validate=OneOf(['SAME','VALID', 'CAUSAL']),
                                description='Padding type used in convolutions.')
        pooling = fields.String(missing='SAME', validate=OneOf(['SAME','VALID', 'CAUSAL']),
                                  description='Padding type used in convolutions.')

        activation = fields.Str(missing='relu', description='Activation function used in final filters.')

        kernel_initializer = fields.Str(missing='he_normal', description='Method to initialise kernel parameters.')
        kernel_regularizer = fields.Float(missing=1e-6, description='L2 regularization parameter.')
        enumerate = fields.Bool(missing=False, description='Increase number of filters across convolution')
        batch_norm = fields.Bool(missing=False, description='Whether to use batch normalisation.')

        rnn_layer = fields.String(required=True, validate=OneOf(['rnn', 'lstm', 'gru']),
                                  description='Type of RNN layer to use')

        rnn_units = fields.Int(missing=64, description='Size of the convolution kernels.')
        rnn_blocks = fields.Int(missing=1, description='Number of LSTM blocks')
        bidirectional = fields.Bool(missing=True, description='Whether to use a bidirectional layer')

        layer_norm = fields.Bool(missing=True, description='Whether to apply layer normalization in the encoder.')

    def _cnn_layer(self):


    def _cnn_layer(self, net, i = 0):

        dropout_rate = 1 - self.config.keep_prob
        filters = self.config.nb_conv_filters
        kernel_size = self.config.kernel_size

        if self.config.enumerate:
            filters = filters * (2**i)
            kernel_size = kernel_size * (i+1)

        layer =  tf.keras.layers.Conv2D(
            filters, kernel_size, strides=(1, 1), padding='valid',
            data_format=None, dilation_rate=(1, 1), groups=1, activation=None,
            use_bias=True, kernel_initializer='glorot_uniform',
            bias_initializer='zeros', kernel_regularizer=None,
            bias_regularizer=None, activity_regularizer=None, kernel_constraint=None,
            bias_constraint=None, **kwargs
        )(net)

        if self.config.batch_norm:
            layer = tf.keras.layers.BatchNormalization(axis=-1)(layer)

        #if self.config.enumerate: layer = tf.keras.layers.MaxPool1D()(layer)

        layer = tf.keras.layers.Dropout(dropout_rate)(layer)
        layer = tf.keras.layers.Activation(self.config.activation)(layer)
        return layer


    def _rnn_layer(self, last=False):
        """ Returns a RNN layer for current configuration. Use `last=True` for the last RNN layer. """
        RNNLayer = rnn_layers[self.config.rnn_layer]
        dropout_rate = 1 - self.config.keep_prob

        layer = RNNLayer(
            units=self.config.rnn_units,
            dropout=dropout_rate,
            return_sequences=not last,
        )

        # Use bidirectional if specified
        if self.config.bidirectional:
            layer = tf.keras.layers.Bidirectional(layer)

        return layer

    def _fcn_layer(self):
        dropout_rate = 1 - self.config.keep_prob
        layer_fcn = Dense(units=self.config.nb_fc_neurons,
                          kernel_initializer=self.config.kernel_initializer,
                          kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))
        if self.config.batch_norm:
            layer_fcn = tf.keras.layers.BatchNormalization(axis=-1)(layer_fcn)

        layer_fcn = tf.keras.layers.Dropout(dropout_rate)(layer_fcn)

        return layer_fcn

    def init_model(self):
        """ Creates the RNN model architecture. """

        layers = []
        if self.config.layer_norm:
            layer_norm = tf.keras.layers.LayerNormalization()
            layers.append(layer_norm)

        # RNN layers
        layers.extend([self._rnn_layer() for _ in range(self.config.rnn_blocks-1)])
        layers.append(self._rnn_layer(last=True))

        if self.config.batch_norm:
            batch_norm = tf.keras.layers.BatchNormalization()
            layers.append(batch_norm)

        if self.config.layer_norm:
            layer_norm = tf.keras.layers.LayerNormalization()
            layers.append(layer_norm)

        layers.extend([self._fcn_layer() for _ in range(self.config.nb_fc_stacks)])

        dense = tf.keras.layers.Dense(units=1,
                                      activation='linear',
                                      kernel_initializer=self.config.kernel_initializer,
                                      kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))

        layers.append(dense)

        self.net = tf.keras.Sequential(layers)

    def build(self, inputs_shape):
        self.net.build(inputs_shape)

        print_summary(self.net)

    def call(self, inputs, training=None):
        return self.net(inputs, training)



class CNNTRransformer(BaseRegressionModel):
    # https://www.sciencedirect.com/science/article/pii/S0034425721003205 ~ having like image conv MHA (input, time, h, w, c)
    """ Implementation of a Bidirectional Recurrent Neural Network

    This implementation allows users to define which RNN layer to use, e.g. SimpleRNN, GRU or LSTM
    """


    class ConvLSTMShema(BaseRegressionModel._Schema):
        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)
        kernel_size = fields.Int(missing=5, description='Size of the convolution kernels.')
        nb_conv_filters = fields.Int(missing=16, description='Number of convolutional filters.')
        nb_conv_stacks = fields.Int(missing=3, description='Number of convolutional blocks.')
        nb_conv_strides = fields.Int(missing=1, description='Value of convolutional strides.')
        nb_fc_neurons = fields.Int(missing=256, description='Number of Fully Connect neurons.')
        nb_fc_stacks = fields.Int(missing=1, description='Number of fully connected layers.')

        final_layer = fields.String(missing='Flatten', validate=OneOf(['Flatten','GlobalAveragePooling1D', 'GlobalMaxPooling1D']),
                                    description='Final layer after the convolutions.')
        padding = fields.String(missing='SAME', validate=OneOf(['SAME','VALID', 'CAUSAL']),
                                description='Padding type used in convolutions.')
        pooling = fields.String(missing='SAME', validate=OneOf(['SAME','VALID', 'CAUSAL']),
                                  description='Padding type used in convolutions.')

        activation = fields.Str(missing='relu', description='Activation function used in final filters.')


    def _cnn_layer(self, net, i = 0):

        dropout_rate = 1 - self.config.keep_prob
        filters = self.config.nb_conv_filters
        kernel_size = self.config.kernel_size

        if self.config.enumerate:
            filters = filters * (2**i)
            kernel_size = kernel_size * (i+1)

        layer =  tf.keras.layers.Conv2D(
            filters, kernel_size, strides=(1, 1), padding='valid',
            data_format=None, dilation_rate=(1, 1), groups=1, activation=None,
            use_bias=True, kernel_initializer='glorot_uniform',
            bias_initializer='zeros', kernel_regularizer=None,
            bias_regularizer=None, activity_regularizer=None, kernel_constraint=None,
            bias_constraint=None, **kwargs
        )(net)

        if self.config.batch_norm:
            layer = tf.keras.layers.BatchNormalization(axis=-1)(layer)

        #if self.config.enumerate: layer = tf.keras.layers.MaxPool1D()(layer)

        layer = tf.keras.layers.Dropout(dropout_rate)(layer)
        layer = tf.keras.layers.Activation(self.config.activation)(layer)
        return layer


    def _fcn_layer(self):
        dropout_rate = 1 - self.config.keep_prob
        layer_fcn = Dense(units=self.config.nb_fc_neurons,
                          kernel_initializer=self.config.kernel_initializer,
                          kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))
        if self.config.batch_norm:
            layer_fcn = tf.keras.layers.BatchNormalization(axis=-1)(layer_fcn)

        layer_fcn = tf.keras.layers.Dropout(dropout_rate)(layer_fcn)

        return layer_fcn

    def init_model(self):
        """ Creates the RNN model architecture. """

        layers = []
        if self.config.layer_norm:
            layer_norm = tf.keras.layers.LayerNormalization()
            layers.append(layer_norm)

        # RNN layers
        layers.extend([self._rnn_layer() for _ in range(self.config.rnn_blocks-1)])
        layers.append(self._rnn_layer(last=True))

        if self.config.batch_norm:
            batch_norm = tf.keras.layers.BatchNormalization()
            layers.append(batch_norm)

        if self.config.layer_norm:
            layer_norm = tf.keras.layers.LayerNormalization()
            layers.append(layer_norm)

        layers.extend([self._fcn_layer() for _ in range(self.config.nb_fc_stacks)])

        dense = tf.keras.layers.Dense(units=1,
                                      activation='linear',
                                      kernel_initializer=self.config.kernel_initializer,
                                      kernel_regularizer=tf.keras.regularizers.l2(self.config.kernel_regularizer))

        layers.append(dense)

        self.net = tf.keras.Sequential(layers)

    def build(self, inputs_shape):
        self.net.build(inputs_shape)

        print_summary(self.net)

    def call(self, inputs, training=None):
        return self.net(inputs, training)





class TransformerEncoder(BaseClassificationModel):
    """ Implementation of a self-attention classifier
    Code is based on the Pytorch implementation of Marc Russwurm https://github.com/MarcCoru/crop-type-mapping
    """

    class TransformerEncoderSchema(BaseClassificationModel._Schema):
        keep_prob = fields.Float(required=True, description='Keep probability used in dropout layers.', example=0.5)

        num_heads = fields.Int(missing=8, description='Number of Attention heads.')
        num_layers = fields.Int(missing=4, description='Number of encoder layers.')
        num_dff = fields.Int(missing=512, description='Number of feed-forward neurons in point-wise MLP.')
        d_model = fields.Int(missing=128, description='Depth of model.')
        max_pos_enc = fields.Int(missing=24, description='Maximum length of positional encoding.')
        layer_norm = fields.Bool(missing=True, description='Whether to apply layer normalization in the encoder.')

        activation = fields.Str(missing='linear', description='Activation function used in final dense filters.')

    def init_model(self):

        self.encoder = transformer_encoder_layers.Encoder(
            num_layers=self.config.num_layers,
            d_model=self.config.d_model,
            num_heads=self.config.num_heads,
            dff=self.config.num_dff,
            maximum_position_encoding=self.config.max_pos_enc,
            layer_norm=self.config.layer_norm)

        self.dense = tf.keras.layers.Dense(units=self.config.n_classes,
                                           activation=self.config.activation)

    def build(self, inputs_shape):
        """ Build Transformer encoder architecture
        The `inputs_shape` argument is a `(N, T, D)` tuple where `N` denotes the number of samples, `T` the number of
        time-frames, and `D` the number of channels
        """
        seq_len = inputs_shape[1]

        self.net = tf.keras.Sequential([
            self.encoder,
            self.dense,
            tf.keras.layers.MaxPool1D(pool_size=seq_len),
            tf.keras.layers.Lambda(lambda x: tf.keras.backend.squeeze(x, axis=-2), name='squeeze'),
            tf.keras.layers.Softmax()
        ])
        # Build the model, so we can print the summary
        self.net.build(inputs_shape)

        print_summary(self.net)

    def call(self, inputs, training=None, mask=None):
        return self.net(inputs, training, mask)





########################################################################################################################################################

class PseTae(BaseRegressionModel):
    """ Implementation of the Pixel-Set encoder + Temporal Attention Encoder sequence classifier

    Code is based on the Pytorch implementation of V. Sainte Fare Garnot et al. https://github.com/VSainteuf/pytorch-psetae
    """

    class PseTaeSchema(BaseRegressionModel._Schema):
        mlp1 = fields.List(fields.Int, missing=[10, 32, 64], description='Number of units for each layer in mlp1.')
        pooling = fields.Str(missing='mean_std', description='Methods used for pooling. Seperated by underscore. (mean, std, max, min)')
        mlp2 = fields.List(fields.Int, missing=[132, 128], description='Number of units for each layer in mlp2.')

        num_heads = fields.Int(missing=4, description='Number of Attention heads.')
        num_dff = fields.Int(missing=32, description='Number of feed-forward neurons in point-wise MLP.')
        d_model = fields.Int(missing=None, description='Depth of model.')
        mlp3 = fields.List(fields.Int, missing=[512, 128, 128], description='Number of units for each layer in mlp3.')
        dropout = fields.Float(missing=0.2, description='Dropout rate for attention encoder.')
        T = fields.Float(missing=1000, description='Number of features for attention.')
        len_max_seq = fields.Int(missing=24, description='Number of features for attention.')
        mlp4 = fields.List(fields.Int, missing=[128, 64, 32], description='Number of units for each layer in mlp4. ')

    def init_model(self):
        # TODO: missing features from original PseTae:
        #   * spatial encoder extra features (hand-made)
        #   * spatial encoder masking

        self.spatial_encoder = pse_tae_layers.PixelSetEncoder(
            mlp1=self.config.mlp1,
            mlp2=self.config.mlp2,
            pooling=self.config.pooling)

        self.temporal_encoder = pse_tae_layers.TemporalAttentionEncoder(
            n_head=self.config.num_heads,
            d_k=self.config.num_dff,
            d_model=self.config.d_model,
            n_neurons=self.config.mlp3,
            dropout=self.config.dropout,
            T=self.config.T,
            len_max_seq=self.config.len_max_seq)

        mlp4_layers = [pse_tae_layers.LinearLayer(out_dim) for out_dim in self.config.mlp4]
        # Final layer (logits)
        mlp4_layers.append(pse_tae_layers.LinearLayer(1, batch_norm=False, activation='linear'))

        self.mlp4 = tf.keras.Sequential(mlp4_layers)

    def call(self, inputs, training=None, mask=None):

        out = self.spatial_encoder(inputs, training=training, mask=mask)
        out = self.temporal_encoder(out, training=training, mask=mask)
        out = self.mlp4(out, training=training, mask=mask)

        return out
