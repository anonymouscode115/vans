from voi.nn.wrappers.layer import Layer
from voi.nn.base.block import Block
from voi.nn.base.sequence_to_mat import SequenceToMat
from voi.nn.base.stick_breaking import StickBreaking
from voi.nn.input import AttentionInput
import tensorflow as tf
import tensorflow_probability as tfp


class PermutationLayer(Layer):

    def __init__(self,
                 input_size,
                 temperature=1.,
                 **kwargs):
        """Creates a Transformer permutation layer by applying a multi
        head sequence to matrix layer; and then applying sinkhorn
        normalization to the activations

        Arguments:

        input_size: int
            the number of units in the input variables used
            in the sequence to matrix layer
        temperature: float
            a positive number to divide the permutation logits by prior
            to applying normaliozation"""
        super(PermutationLayer, self).__init__()

        # the core attention and processing variables
        self.stick_breaking = StickBreaking()
        self.sequence_to_mat = SequenceToMat(
            input_size=input_size)

        # these parameters need to be stored so that
        # tf.layers.model.save_model works
        self.input_size = input_size
        self.temperature = temperature
        self.kwargs = kwargs

    def call(self, inputs, **kwargs):
        """Runs a forward pass on a multi head attention layer
        inputs is an instance of TransformerInput

        Arguments:

        inputs: TransformerInput
            a dataclass instance that contains queries, keys
            and values along with masks

        Returns:

        permutation: TransformerInput
            the result of applying a sequence to matrix layer and
            sinkhorn normalization; a doubly stochastic matrix
            with shape [batch, seq_length, seq_length]"""

        # process the transformer hidden states using a sequence to matrix
        # layer that performs an H W_x H^T op
        [queries, values, queries_mask, values_mask,
         _, _, _, _, _, _, _, _, _,
         object_detections, object_features, object_boxes] = inputs
        log_s, v = self.sequence_to_mat.static_call(queries, queries, 
                                                    queries_mask, queries_mask, **kwargs)

        # apply a mask to the scores matrix so that only real
        # non terminal elements are permuted out of place
        mask = tf.logical_and(tf.expand_dims(queries_mask, -2),
                              tf.expand_dims(queries_mask, -1))

        # pad tokens should not be permuted and logits on the diagonal
        # for pad tokens should not be masked out; this is necessary because
        # a valid permutation matrix has rows and columns that sum to one,
        # even for rows that correspond to pad tokens
        eye = tf.eye(tf.shape(mask)[-2], num_columns=tf.shape(mask)[-1],
                     batch_shape=tf.shape(mask)[:-2], dtype=tf.bool)
        eye_mask = tf.cast(tf.logical_or(mask, eye), tf.float32)

        # pass the outputs of the attention through a normalization layer
        # that performs stick breaking normalization
        mask = tf.cast(mask, tf.float32)
        mean = (tf.reduce_sum(v * mask, axis=[1, 2], keepdims=True) /
                tf.reduce_sum(mask, axis=[1, 2], keepdims=True))

        # create a gaussian distribution to sample noise from
        noise = tfp.distributions.MultivariateNormalDiag(
            loc=v - mean, scale_diag=mask * tf.exp(log_s - 2.))

        # pass the noise through a stick breaking normalization function
        # and calculate entropy of the doubly stochastic matrix
        return self.stick_breaking([
            noise.sample() / self.temperature, eye_mask], **kwargs)

    def get_config(self):
        """Creates a state dictionary that can be used to rebuild
        the layer in another python process

        Returns:

        config: dict
            a dictionary that contains all parameters to the
            layers base class and all class parameters"""

        # these are all that is needed to rebuild this class
        config = dict(input_size=self.input_size,
                      temperature=self.temperature,
                      ** self.kwargs)

        base_config = super(PermutationLayer, self).get_config()
        return dict(list(base_config.items()) +
                    list(config.items()))
