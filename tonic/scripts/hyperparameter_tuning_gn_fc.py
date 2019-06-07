"""
MIT License

Copyright (c) 2019 Chodera lab // Memorial Sloan Kettering Cancer Center,
Weill Cornell Medical College, Nicea Research, and Authors

Authors:
Yuanqing Wang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

"""

import tensorflow as tf
tf.enable_eager_execution()
import gin
import tonic
import time
import pandas as pd
import numpy as np

df = pd.read_csv('data/delaney-processed.csv')
x_array = df[['smiles']].values.flatten()
y_array = df[['measured log solubility in mols per litre']].values.flatten()
y_array = (y_array - np.mean(y_array) / np.std(y_array))
n_samples = y_array.shape[0]

ds_all = gin.i_o.from_smiles.smiles_to_mols_with_attributes(x_array, y_array)
ds_all = ds_all.shuffle(n_samples)

n_global_te = int(0.2 * n_samples)
ds = ds_all.skip(n_global_te)
ds_global_te = ds_all.take(n_global_te)


config_space = {
    'f_e_0': [32, 64, 128, 256],
    'f_v_0': [32, 64, 128, 256],
    'f_u_0': [32, 64, 128, 256],

    'phi_e_0': [32, 64, 128, 256],
    'phi_e_a_0': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],
    'phi_e_a_1': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],

    'phi_v_0': [32, 64, 128, 256],
    'phi_v_a_0': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],
    'phi_v_a_1': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],

    'phi_u_0': [32, 64, 128, 256],
    'phi_u_a_0': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],
    'phi_u_a_1': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],

    'f_r_0': [32, 64, 128, 256],
    'f_r_a': ['elu', 'relu', 'leaky_relu', 'tanh', 'sigmoid'],
    'f_r_1': [32, 64, 128, 256],

    'learning_rate': [1e-5, 1e-4, 1e-3, 1e-2]
}



def obj_fn(point):
    point = dict(zip(config_space.keys(), point))
    n_te = int(0.2 * 0.8 * n_samples)
    ds = ds_all.shuffle(n_samples)

    mse_train = []
    mse_test = []

    for idx in range(5):

        ds_tr = ds.take(idx * n_te).concatenate(
            ds.skip((idx + 1) * n_te).take((4 - idx) * n_te))
        ds_te = ds.skip(idx * n_te).take((idx + 1) * n_te)

        class f_r(tf.keras.Model):
            def __init__(self, config):
                super(f_r, self).__init__()
                self.d = tonic.nets.for_gn.ConcatenateThenFullyConnect(config)

            @tf.function
            def call(self, h_e, h_v, h_u):
                y = self.d(h_u)[0][0]
                return y

        class f_v(tf.keras.Model):
            def __init__(self, units):
                super(f_v, self).__init__()
                self.d = tf.keras.layers.Dense(units)

            @tf.function
            def call(self, x):
                return self.d(tf.one_hot(x, 8))

        gn = gin.probabilistic.gn.GraphNet(
            f_e=tf.keras.layers.Dense(point['f_e_0']),

            f_v=f_v(point['f_v_0'])

            f_u=(lambda x, y: tf.zeros((1, point['f_u_0']), dtype=tf.float32)),

            phi_e=tonic.nets.for_gn.ConcatenateThenFullyConnect(
                (point['phi_e_0'],
                 point['phi_e_a_0'],
                 point['f_e_0'],
                 point['phi_e_a_1'])),

            phi_v=tonic.nets.for_gn.ConcatenateThenFullyConnect(
                (point['phi_v_0'],
                 point['phi_v_a_0'],
                 point['f_v_0'],
                 point['phi_v_a_1'])),

            phi_u=tonic.nets.for_gn.ConcatenateThenFullyConnect(
                (point['phi_u_0'],
                 point['phi_u_a_0'],
                 point['f_u_0'],
                 point['phi_u_a_1'])),
            f_r=f_r((point['f_r_0'], point['f_r_a'], point['f_r_1'], 1)))

        optimizer = tf.train.AdamOptimizer(point['learning_rate'])
        n_epoch = 30
        batch_size = 32
        batch_idx = 0
        loss = 0
        tape = tf.GradientTape()

        for dummy_idx in range(n_epoch):
            for atoms, adjacency_map, y in ds_tr:
                mol = [atoms, adjacency_map]

                with tape:
                    y_hat = gn(mol)
                    loss += tf.pow(y - y_hat, 2)
                    batch_idx += 1

                if batch_idx == batch_size:
                    variables = gn.variables
                    grad = tape.gradient(loss, variables)
                    optimizer.apply_gradients(
                        zip(grad, variables),
                        tf.train.get_or_create_global_step())
                    loss = 0
                    batch_idx = 0
                    tape = tf.GradientTape()

        gn.switch(True)

        # test on train data
        mse_train.append(tf.reduce_mean(
            [(gn([atoms, adjacency_map]) - y) \
                for atoms, adjacency_map, y in ds_tr]))
        mse_train.append(tf.reduce_mean(
            [(gn([atoms, adjacency_map]) - y) \
                for atoms, adjacency_map, y in ds_tr]))


        class f_r(tf.keras.Model):
            def __init__(self, config):
                super(f_r, self).__init__()
                self.d = tonic.nets.for_gn.ConcatenateThenFullyConnect(config)

            @tf.function
            def call(self, h_e, h_v, h_u):
                y = self.d(h_u)[0][0]
                return y

        class f_v(tf.keras.Model):
            def __init__(self, units):
                super(f_v, self).__init__()
                self.d = tf.keras.layers.Dense(units)

            @tf.function
            def call(self, x):
                return self.d(tf.one_hot(x, 8))

        gn = gin.probabilistic.gn.GraphNet(
            f_e=tf.keras.layers.Dense(point['f_e_0']),

            f_v=f_v(point['f_v_0'])

            f_u=(lambda x, y: tf.zeros((1, point['f_u_0']), dtype=tf.float32)),

            phi_e=tonic.nets.for_gn.ConcatenateThenFullyConnect(
                (point['phi_e_0'],
                 point['phi_e_a_0'],
                 point['f_e_0'],
                 point['phi_e_a_1'])),

            phi_v=tonic.nets.for_gn.ConcatenateThenFullyConnect(
                (point['phi_v_0'],
                 point['phi_v_a_0'],
                 point['f_v_0'],
                 point['phi_v_a_1'])),

            phi_u=tonic.nets.for_gn.ConcatenateThenFullyConnect(
                (point['phi_u_0'],
                 point['phi_u_a_0'],
                 point['f_u_0'],
                 point['phi_u_a_1'])),
            f_r=f_r((point['f_r_0'], point['f_r_a'], point['f_r_1'], 1)))

    optimizer = tf.train.AdamOptimizer(point['learning_rate'])
    n_epoch = 30
    batch_size = 32
    batch_idx = 0
    loss = 0
    tape = tf.GradientTape()

    time0 = time.time()
    for dummy_idx in range(n_epoch):
        for atoms, adjacency_map, y in ds:
            mol = [atoms, adjacency_map]

            with tape:
                y_hat = gn(mol)
                loss += tf.clip_by_norm(
                    tf.losses.mean_squared_error(y, y_hat),
                    1e8)
                batch_idx += 1

            if batch_idx == batch_size:
                variables = gn.variables
                grad = tape.gradient(loss, variables)
                optimizer.apply_gradients(
                    zip(grad, variables),
                    tf.train.get_or_create_global_step())
                loss = 0
                batch_idx = 0
                tape = tf.GradientTape()
    time1 = time.time()

    mse_global_test = tf.reduce_mean(
        [(gn([atoms, adjacency_map]) - y) \
            for atoms, adjacency_map, y in ds_global_te])

    mse_train = tf.reduce_mean(mse_train)
    mse_test = tf.reduce_mean(mse_test)

    print(point)
    print('training time %s ' % (time1 - time0))
    print('mse_train %s' % mse_train.numpy())
    print('mse_test %s' % mse_test.numpy())
    print('mse_global_test %s' % mse_global_test.numpy())
    print('n_params %s ' % gn.count_params())

    return mse_test


tonic.optimize.dummy.optimize(obj_fn, config_space.values(), 1000)
