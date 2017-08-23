from keras.models import Model
import keras.layers
import numpy as np
import pandas as pd
import json

def _test_loop(self, f, ins, out_labels=None, batch_size=32, verbose=0):
    from keras.engine.training import _make_batches, _slice_arrays
    import keras.callbacks as cbks
    from utils import Model_state
    if ins and hasattr(ins[0], 'shape'):
        samples = ins[0].shape[0]
    else:
        samples = batch_size
        verbose = 2
    # callbacks = cbks.CallbackList(callbacks)
    out_labels = out_labels or []
    
    # add callback support to evaluate function
    # state_file = '/home/saturn/research/petpen/models/state.json'
    state_file = '/home/plash/petpen/state.json'
    config = {'loss':self.loss}
    callbacks = Model_state(state_file,config)
    callbacks.set_model(self)
    callbacks.set_params({
        'batch_size': batch_size,
        'samples': samples,
        'verbose': verbose,
    })
    callbacks.on_test_begin()
    outs = []
    batches = _make_batches(samples, batch_size)
    index_array = np.arange(samples)
    for batch_index, (batch_start, batch_end) in enumerate(batches):
        batch_ids = index_array[batch_start:batch_end]
        if isinstance(ins[-1], float):
            ins_batch = _slice_arrays(ins[:-1], batch_ids) + [ins[-1]]
        else:
            ins_batch = _slice_arrays(ins, batch_ids)
        batch_logs = {}
        batch_logs['batch'] = batch_index
        batch_logs['size'] = len(batch_ids)
        callbacks.on_batch_begin(batch_index, batch_logs)
        batch_outs = f(ins_batch)
        if isinstance(batch_outs, list):
            if batch_index == 0:
                for batch_out in enumerate(batch_outs):
                    outs.append(0.)
            for i, batch_out in enumerate(batch_outs):
                outs[i] +=batch_out * len(batch_ids)
        else:
            if batch_index == 0:
                outs.append(0.)
            outs[0] += batch_outs * len(batch_ids)
    for i in range(len(outs)):
        outs[i] /= samples
    callbacks.on_test_end(outs)
    if len(outs) == 1:
        return outs[0]
    return outs

def replaceMethod(self):
    def func(*args,**kwargs):
        return _test_loop(self,*args,**kwargs)
    return func

class backend_model():
    def __init__(self,file_path):
        self.model,self.config = get_model(file_path)
        self.loss = self.config.get('loss') or None
        self.optimizer = self.config.get('optimizer') or None
        self.model.compile(loss=self.loss, optimizer=self.optimizer)
        self.batch_size = self.config.get('batch_size') or self.config.get('batchsize')
        self.epochs = self.config.get('epochs') or self.config.get('epoch')
        self.model._test_loop = replaceMethod(self.model)
        self.callbacks = []
        # self.load_dataset(file_path)
    
    def load_dataset(self,file_path):
        with open(file_path) as f:
            dataset_config = json.load(f)['dataset']
        self.dataset = pd.read_csv(dataset_config['file_path'])
    
    def train(self,train_data,train_target,**kwargs):
        if 'callbacks' in kwargs:
            return self.model.fit(train_data, train_target, batch_size=self.batch_size,
                epochs=self.epochs,
                callbacks=kwargs['callbacks'],
                initial_epoch=0)
        return self.model.fit(train_data, train_target,
                batch_size=self.batch_size,
                epochs=self.epochs,)
    def evaluate(self,test_data,test_target):
        return self.model.evaluate(test_data, test_target, batch_size=self.batch_size)
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
        self.model.load_weights(file_path)

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
    return model, config

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
