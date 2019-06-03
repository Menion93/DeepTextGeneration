import tensorflow.keras as keras
import tensorflow as tf
import numpy as np
from pointer_components import Encoder, Decoder, Attention, PointerSwitch
tf.enable_eager_execution()


class PointerNetwork(keras.Model):
    def __init__(self,
                 enc_units,
                 dec_units,
                 voc_size,
                 att_units,
                 switch_units,
                 max_len,
                 start_token,
                 end_token):
        super().__init__()
        self.encoder = Encoder(enc_units)
        self.decoder = Decoder(dec_units, voc_size)
        self.attention = Attention(att_units)
        self.pointer_switch = PointerSwitch(switch_units)
        self.embeddings = False
        self.max_len = max_len
        self.start_token = start_token
        self.end_token = end_token
        self.voc_size = voc_size

        self.optimizer = tf.train.AdamOptimizer()

    def set_embeddings_layer(self, embeddings_layer):
        self.embeddings = embeddings_layer

    def predict_batch(self, X):
        assert self.embeddings, "Call self.set_embeddings_layer first"
        X = tf.convert_to_tensor(X)

        embed = self.embeddings(X)
        enc_states, h1, h2 = self.encoder(embed)
        input_tokens = tf.convert_to_tensor(
            [self.start_token] * embed.shape[0])
        # put last encoder state as attention vec at start
        c_vec = h1
        outputs = []

        for _ in range(self.max_len):
            dec_input = self.embeddings(input_tokens)
            decoded_state, h1, h2, decoded_probs = self.decoder(dec_input,
                                                                c_vec,
                                                                [h1, h2])
            c_vec, pointer_probs = self.attention(enc_states,
                                                  decoded_state)

            # Compute switch probability to decide where to extract the next
            # word token
            switch_probs = self.pointer_switch(h1, c_vec)
            # Decode based on switch probs
            input_tokens = self.decode_next_word(switch_probs,
                                                 decoded_probs,
                                                 X,
                                                 pointer_probs)
            outputs.append(input_tokens)

        return tf.transpose(tf.convert_to_tensor(outputs))

    def decode_next_word(self, switch_probs, decoded_probs, inputs, att_probs):
        sampled_probs = tf.random.uniform(switch_probs.shape, 0, 1)
        tokens = []
        token = None

        for prob, sampled, decoded, inp, att_p in zip(switch_probs,
                                                      sampled_probs,
                                                      decoded_probs,
                                                      inputs,
                                                      att_probs):
            if prob.numpy()[0] >= sampled.numpy()[0]:
                token = self.fixed_vocab_decode(decoded)
            else:
                token = self.pointer_greedy_search(att_p, inp)

            tokens.append(token)

        return tf.convert_to_tensor(tokens, dtype=tf.float32)

    def pointer_greedy_search(self, probs, inputs):
        return inputs[tf.argmax(probs)]

    def fixed_vocab_decode(self, decoded_probs):
        return tf.argmax(decoded_probs)

    def pointer_batch_loss(self, gen, y, d_prob, p_prob, s_prob):

        pointer_mat = p_prob + (1 - s_prob)
        generator_mat = d_prob + s_prob

        batch_loss = 0
        for i, g in enumerate(gen):
            if g == 0:
                batch_loss += pointer_mat[i, y[i]]
            else:
                batch_loss += generator_mat[i, y[i]]

        # Reduce to scalar, dont forget to include minus sign (its a loss not a likelihood)
        return -batch_loss

    def __train_batch(self, X, y, gen):
        assert self.embeddings, "Call self.load_embeddings first"

        X = tf.convert_to_tensor(X)
        y = tf.convert_to_tensor(y, dtype='int32')
        gen = tf.convert_to_tensor(gen, dtype='float32')

        enc_inp = self.embeddings(X)
        enc_states, h1, h2 = self.encoder(enc_inp)
        c_vec = h1
        input_tokens = tf.convert_to_tensor(
            [self.start_token] * enc_inp.shape[0])
        loss = 0
        for t in range(y.shape[1]):
            # Get embeddings
            dec_input = self.embeddings(input_tokens)

            # Get decoder output
            decoded_state, h1, h2, decoded_probs = self.decoder(
                dec_input, c_vec, [h1, h2])

            # Get context vector for the next step, and pointer probabilities
            c_vec, pointer_probs = self.attention(enc_states, decoded_state)

            # Get switch probability (BS*1)
            switch_probs = self.pointer_switch(h1, c_vec)

            # Is target generated or extracted from the input (BS * 1)
            batch_gen = tf.convert_to_tensor(gen[:, t])

            # Compute Pointer Network batch loss at timestep t
            loss += self.pointer_batch_loss(batch_gen, y[:, t], decoded_probs,
                                            pointer_probs, switch_probs)

            # Get next decoder input tokens
            input_tokens = y[:, t]

        # Dont forget to divide by summary lenght N, since we lose the /N component n by calling
        # N times softmax cross entropy
        loss = loss / int(y.shape[1])
        return loss

    def train_batch(self, X, y, gen):
        self.optimizer.minimize(lambda: self.__train_batch(X, y, gen))

    def get_all_variables(self):
        return self.encoder.get_variables() + \
            self.decoder.get_variables() + \
            self.attention.get_variables() + \
            self.pointer_switch.get_variables()
