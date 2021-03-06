from keras.models import Model
import keras.layers
import numpy as np
import pandas as pd
import json

class backend_model():
    def __init__(self, model_path, data_path):
        self.model,self.config,self.inputs,self.outputs = get_model(model_path)
        self.loss = self.config.get('loss') or None
        self.optimizer = self.config.get('optimizer') or None
        self.model.compile(loss=self.loss, optimizer=self.optimizer)
        self.batch_size = self.config.get('batch_size') or self.config.get('batchsize')
        self.epochs = self.config.get('epochs') or self.config.get('epoch')
        self.callbacks = []

    def load_dataset(self,model_path,dataset_path,validate_rate=0.3,shuffle=False):
        """
        load dataset from the given config json file generated by node-red.
        json file structure:
        {
            layers: ...
            connections: ...
            dataset: {
                file_path: ...
                input_name: ...
                output_name: ...
            }
        }


        TODO: shuffle not implemented

        """

        with open(model_path) as f:
            dataset_config = json.load(f)['dataset']
            dataset_config['file_path'] = dataset_path
        dataset = pd.read_csv(dataset_config['file_path'])
        self.sample_size = dataset.shape[0]
        self.train_sample_size = self.sample_size*(1-validate_rate)
        self.validate_sample_size = self.sample_size*validate_rate

        self.train_x,self.train_y = [],[]
        self.valid_x,self.valid_y = [],[]

        # extract feature names
        for _in in self.inputs:
            # ':' means all columns, ':,a,b,...' means all except a,b,...
            features = dataset_config.get(_in)
            if features[0] == ':':
                if len(features) == 1:
                    features = dataset.columns
                else:
                    features = dataset.columns.difference(features[1:])
            self.train_x.append(dataset.loc[:self.train_sample_size,features].values)
            self.valid_x.append(dataset.loc[self.train_sample_size:,features].values)

        # extract target names
        for _out in self.outputs:
            targets = dataset_config.get(_out)
            # ':' means all columns, ':,a,b,...' means all except a,b,...
            if targets[0] == ':':
                if len(targets) == 1:
                    targets = dataset.columns
                else:
                    targets = dataset.columns.difference(targets[1:])

            if self.loss == 'categorical_crossentropy':
                from keras.utils.np_utils import to_categorical
                self.train_y.append(to_categorical(dataset.loc[:self.train_sample_size,targets].values))
                self.valid_y.append(to_categorical(dataset.loc[self.train_sample_size:,targets].values))
            else:
                self.train_y.append(dataset.loc[:self.train_sample_size,targets].values)
                self.valid_y.append(dataset.loc[self.train_sample_size:,targets].values)

    def train(self,**kwargs):
        callbacks = []
        if 'callbacks' in kwargs:
            callbacks = kwargs['callbacks']
        return self.model.fit(self.train_x, self.train_y,
            batch_size=self.batch_size,
            epochs=self.epochs,
            callbacks=callbacks,
            initial_epoch=0)

    def evaluate(self,**kwargs):
        return self.model.evaluate(self.valid_x, self.valid_y,
                batch_size=self.batch_size)

    def predict(self,test_data):
        return self.model.predict(test_data)

    def plot_model(self, file_name='model.png'):
        from keras.utils import plot_model
        plot_model(self.model, to_file=file_name)

    def set_callbacks(self,callbacks):
        self.callbacks = callbacks

    def set_batch_size(self,batch_size):
        self.batch_size = batch_size

    def save_weights(self,file_path):
        self.model.save_weights(file_path)

    def load_weights(self,file_path):
        self.model.load_weights(file_path,by_name=True)

    def save_architecture(self,json_fp):
        json_string = self.model.to_json()
        with open(json_fp,'w') as f:
            f.write(json_string)

def get_model(model_file):
    """
    read model setting from given model json file, then parse to keras model
    """

    import json
    import re

    with open(model_file) as f:
        model_parser = json.load(f)
    connections = model_parser['connections']
    layers = model_parser['layers']

    if len(connections.keys()) < len(layers.keys())-1:
        raise ValueError('some components are not connected!')
    created_layers = {}

    # gather inputs
    inputs = list(filter(lambda layer_name: layers[layer_name]['type']=='Input', layers))

    input_names = inputs
    output_names = []

    if len(inputs) == 0:
        raise ValueError('missing input layer in the model')
    for nn_in in inputs:
        input_params = layers[nn_in]['params']
        created_layers[nn_in] = deserialize_layer(layers[nn_in], name=nn_in)
    model_inputs = list(created_layers.values())
    # gather merge layers
    merges = filter(lambda layer_name: layers[layer_name]['type']=='Merge', layers)
    merge_nodes = {m:[] for m in merges}
    for node in merge_nodes:
        inbound_nodes = list(map(lambda connection: connection[0],filter(lambda connection: node in connection[1], connections.items())))
        if len(inbound_nodes) <= 1:
            raise ValueError('merge layer {} needs more than one inbound nodes'.format(node))
        merge_nodes[node]=inbound_nodes

    # iteratively create layer objects
    model_output = []
    while inputs:
        next_layers = []
        for conn_in in inputs:
            conn_outs = connections[conn_in]
            for conn_out in conn_outs:
                if conn_out in created_layers:
                    continue
                layer_config = layers[conn_out]

                layer_type,layer_params = layers[conn_out]['type'], layers[conn_out]['params']
                if layer_type == 'Merge':
                    inbound_node_names = merge_nodes[conn_out]
                    if set(inbound_node_names).issubset(created_layers.keys()):
                        layer = deserialize_layer(layer_config, name=conn_out)
                        inbound_nodes = [created_layers[node] for node in inbound_node_names]
                        created_layers[conn_out] = layer(inbound_nodes)
                        next_layers.append(conn_out)
                    else:
                        next_layers.append(conn_in)
                elif layer_type == 'Output':
                    model_output.append(created_layers[conn_in])
                    config = layer_params
                    output_names.append(conn_out)
                else:
                    layer = deserialize_layer(layer_config, name=conn_out)
                    inbound_node = created_layers[conn_in]
                    created_layers[conn_out] = layer(inbound_node)
                    next_layers.append(conn_out)
        inputs = next_layers
    model_output = model_output or []
    if not model_output:
        raise ValueError('missing output in model')
    model = Model(model_inputs, model_output)
    return model, config, input_names, output_names

def deserialize_layer(layer_config, name=None):
    layer_type = layer_config.get('type')
    if layer_type is None:
        raise ValueError('Undefined layer type')
    layer_params = layer_config.get('params')
    #check whether the layer type is keras object
    if not hasattr(keras.layers,layer_type):
        pass
    # need to fix the inconsistent parameter name and values in future
    if layer_type == 'Convolution_2D' or layer_type == 'CONVOLUTION_2D':
        layer_type = 'Conv2D'
    elif layer_type == 'LSTM' or layer_type == 'SimpleRNN' or layer_type == 'Lstm':
        if layer_type == 'Lstm': layer_type = 'LSTM'
        condition = layer_params.pop('return_sequence')
        if condition == 'True':
            layer_params['return_sequences'] = True
        else:
            layer_params['return_sequences'] = False
    elif layer_type == 'Reshape':
        layer_params['target_shape'] = layer_params.pop('shape')
    elif layer_type == 'Merge':
        merge_method = layer_params['activation']
        return getattr(keras.layers, merge_method)
    layer = getattr(keras.layers,layer_type)(name = name,**layer_params)
    return layer

def compile_model(model,**kw_args):
    model.compile(**kw_args)

if __name__ == '__main__':
    model = get_model('models/model.json')
    print(model)
    # compile_model(model,)
