import torch
from torch import nn
from torch.nn import functional as F
from torch.nn import init

# LSTM implementation referenced from #https://github.com/jihunchoi/recurrent-batch-normalization-pytorch/blob/master/bnlstm.py

class LSTMCell(nn.Module):

    def __init__(self, input_size, hidden_size, bias=True):
        super(LSTMCell, self).__init__()

        self.input_size = input_size
        self.hidden_size = hidden_size
        self.use_bias = bias

        self.weight_ih = nn.Parameter(torch.FloatTensor(input_size, 4*hidden_size))
        self.weight_hh = nn.Parameter(torch.FloatTensor(hidden_size, 4*hidden_size))
        if self.use_bias:
            self.bias = nn.Parameter(torch.FloatTensor(4*hidden_size))
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        init.orthogonal_(self.weight_ih.data)
        weight_hh_data = torch.eye(self.hidden_size)
        weight_hh_data = weight_hh_data.repeat(1, 4)
        with torch.no_grad():
            self.weight_hh.set_(weight_hh_data)
        
        if self.use_bias:
            init.constant_(self.bias.data, val=0)

    def forward(self, input_, hx):
        """
        Args:
            input_: A (batch, input_size) tensor containing input
                features.
            hx: A tuple (h_0, c_0), which contains the initial hidden
                and cell state, where the size of both states is
                (batch, hidden_size).
        Returns:
            h_1, c_1: Tensors containing the next hidden and cell state.
        """

        h_0, c_0 = hx

        wh_b = torch.addmm(mat1=h_0, mat2=self.weight_hh, input=self.bias)
        wi = torch.mm(input_, self.weight_ih)
        f, i, o, g = torch.split(wh_b + wi,
                                 split_size_or_sections=self.hidden_size, dim=1)
        c_1 = torch.sigmoid(f)*c_0 + torch.sigmoid(i)*torch.tanh(g)
        h_1 = torch.sigmoid(o) * torch.tanh(c_1)
        return h_1, c_1

    def __repr__(self):
        s = '{name}({input_size}, {hidden_size})'
        return s.format(name=self.__class__.__name__, **self.__dict__)


class LSTM(nn.Module):

    """A module that runs multiple steps of LSTM."""

    def __init__(self, cell_class, input_size, hidden_size, num_layers=1,
                 use_bias=True, batch_first=False, dropout=0, **kwargs):
        super(LSTM, self).__init__()
        self.cell_class = cell_class
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.use_bias = use_bias
        self.batch_first = batch_first
        self.dropout = dropout

        for layer in range(num_layers):
            layer_input_size = input_size if layer == 0 else hidden_size
            cell = cell_class(input_size=layer_input_size,
                              hidden_size=hidden_size,
                              **kwargs)
            setattr(self, 'cell_{}'.format(layer), cell)
        self.dropout_layer = nn.Dropout(dropout)
        self.reset_parameters()

    def get_cell(self, layer):
        return getattr(self, 'cell_{}'.format(layer))

    def reset_parameters(self):
        for layer in range(self.num_layers):
            cell = self.get_cell(layer)
            cell.reset_parameters()

    @staticmethod
    def _forward_rnn(cell, input_, length, hx):
        max_time = input_.size(0)
        output = []
        for time in range(max_time):
            h_next, c_next = cell(input_=input_[time], hx=hx)
            mask = (time < length).float().unsqueeze(1).expand_as(h_next)
            h_next = h_next*mask + hx[0]*(1 - mask)
            c_next = c_next*mask + hx[1]*(1 - mask)
            hx_next = (h_next, c_next)
            output.append(h_next)
            hx = hx_next
        output = torch.stack(output, 0)
        return output, hx

    def forward(self, input_, length=None, hx=None):
        # print(input_.shape)
        # if self.batch_first:
        #     input_ = input_.transpose(0, 1)

        # This configure is for [B, C, T]
        input_ = input_.permute(2, 0, 1)

        max_time, batch_size, _ = input_.size()
        if length is None:
            length = torch.LongTensor([max_time] * batch_size)
            if input_.is_cuda:
                device = input_.get_device()
                length = length.cuda(device)
        if hx is None:
            hx = (nn.init.xavier_uniform_(torch.zeros((self.num_layers, batch_size, self.hidden_size)).to(input_.device)),
                  nn.init.xavier_uniform_(torch.zeros((self.num_layers, batch_size, self.hidden_size)).to(input_.device)))
        h_n = []
        c_n = []
        layer_output = None
        for layer in range(self.num_layers):
            cell = self.get_cell(layer)
            hx_layer = (hx[0][layer,:,:], hx[1][layer,:,:])
            
            if layer == 0:
                layer_output, (layer_h_n, layer_c_n) = LSTM._forward_rnn(
                    cell=cell, input_=input_, length=length, hx=hx_layer)
            else:
                layer_output, (layer_h_n, layer_c_n) = LSTM._forward_rnn(
                    cell=cell, input_=layer_output, length=length, hx=hx_layer)
            
            input_ = self.dropout_layer(layer_output)
            h_n.append(layer_h_n)
            c_n.append(layer_c_n)
        output = layer_output
        h_n = torch.stack(h_n, 0)
        c_n = torch.stack(c_n, 0)
        return output, (h_n, c_n)