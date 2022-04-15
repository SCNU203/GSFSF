import io
import pickle
import sys
import time

from tqdm import tqdm

from src.tfm import *
from src.pre_data import *
from src.train_and_evaluate import *
import os
import warnings

from src.warmupLR import GradualWarmupScheduler

warnings.filterwarnings("ignore")
USE_CUDA = torch.cuda.is_available()

size_split = 0.8
PAD_token = 0

transformer_name = "hfl/chinese-electra-180g-base-discriminator"
dropout = 0.5
embedding_size = 128
hidden_size = 768
n_layer = 4
n_epochs = 100
batch_size = 32
path = "models/electra2seq/"
seed = 22
torch.manual_seed(seed)
torch.cuda.manual_seed(seed)
torch.cuda.manual_seed_all(seed)
numpy.random.seed(seed)  # Numpy module.
random.seed(seed)  # Python random module.
torch.manual_seed(seed)
torch.backends.cudnn.benchmark = False
torch.backends.cudnn.deterministic = True

data = load_ape_data("data/train.ape.json")
pairs_trained, generate_nums, copy_nums = transfer_ape_num(data, id_file="data/ape_simple_id.txt")
temp_pairs = []
for p in pairs_trained:
    temp_pairs.append((p[0], from_infix_to_prefix(p[1]), p[2], p[3]))
pairs_trained = temp_pairs

data = load_ape_data("data/test.ape.json")
pairs_tested, _, _ = transfer_ape_num(data, id_file="data/ape_simple_test_id.txt")
temp_pairs = []
for p in pairs_tested:
    temp_pairs.append((p[0], from_infix_to_prefix(p[1]), p[2], p[3]))
pairs_tested = temp_pairs


input_lang, output_lang, train_pairs, test_pairs = prepare_data_bert(pairs_trained, pairs_tested, 5, generate_nums,
                                                                     copy_nums, 'weight/electra/vocab.txt', tree=True)
stuff_size = output_lang.num_start + len(generate_nums) + 4
print(output_lang.index2word, " stuff size:", stuff_size)
print(output_lang.n_words)

input_batches, input_lengths, output_batches, output_lengths, nums_batches, num_stack_batches, num_pos_batches, num_size_batches = prepare_train_batch(
    train_pairs, batch_size)

input_batches_test, input_lengths_test, output_batches_test, output_lengths_test, nums_batches_test, num_stack_batches_test, num_pos_batches_test, _ = prepare_test_batch(
    test_pairs, batch_size)

enc = Encoder(input_dim=input_lang.n_words, emb_dim=hidden_size, hid_dim=hidden_size, n_layers=n_layer,
              model_name=transformer_name, dropout=dropout)
dec = Decoder(hidden_size=hidden_size, n_layers=n_layer, dropout=dropout)

model = Seq2Seq(encoder=enc, decoder=dec, stuff_size=stuff_size, hidden_size=hidden_size,
                output_lang=output_lang)

if USE_CUDA:
    model.cuda()

enc_params = list(map(id, model.encoder.parameters()))
base_params = filter(lambda p: id(p) not in enc_params, model.parameters())

optimizer = torch.optim.AdamW([
    {"params": model.encoder.parameters(), "lr": 2e-5},
    {"params": base_params},
], lr=1e-4)
scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=3,
                                                       verbose=True, threshold=0.1, threshold_mode='rel')
warmup_scheduler = GradualWarmupScheduler(optimizer, multiplier=1, total_epoch=10, after_scheduler=scheduler)

best_valid_loss = float('inf')
best_vacc = -1
best_equacc = -1
tt = -1
for epoch in range(n_epochs):
    teacher_forcing = 1
    print("******************************************")
    print('Epoch ', epoch + 1)
    print('learning rate:', optimizer.state_dict()['param_groups'][0]['lr'], " || ",
          optimizer.state_dict()['param_groups'][1]['lr'])
    start = time.time()

    tloss = 0
    t = range(len(input_lengths))
    for idx in t:
        loss = train(input_batches[idx], input_lengths[idx], output_batches[idx], output_lengths[idx],
                     num_stack_batches[idx], output_lang, num_pos_batches[idx],
                     model, optimizer, teacher_forcing)
        tloss += loss
    print("training time", time_since(time.time() - start))
    tloss /= len(input_lengths)
    print('Train Loss', tloss)
    warmup_scheduler.step(metrics=tloss)

    with torch.no_grad():
        value_ac = 0
        equation_ac = 0
        eval_total = 0
        tloss = 0
        start = time.time()
        for idx in range(len((input_lengths_test))):

            out = evaluate(input_batches_test[idx], input_lengths_test[idx], output_batches_test[idx],
                           output_lang, num_pos_batches_test[idx], model)

            for i in range(out.shape[0]):
                eval_total += 1

                val_ac, equ_ac, _, _ = compute_prefix_tree_result(out[i].tolist(), output_batches_test[idx][i],
                                                                  output_lang, nums_batches_test[idx][i],
                                                                  num_stack_batches_test[idx][i])
                if val_ac:
                    value_ac += 1
                if equ_ac:
                    equation_ac += 1
        print("testing time", time_since(time.time() - start))
        print('test: ', equation_ac, value_ac, eval_total, value_ac / eval_total)

    if best_vacc < value_ac:
        best_vacc = value_ac
        best_equacc = equation_ac
        tt = eval_total

        torch.save(model.state_dict(), path + "tfm_ape_" + str(n_layer) + "layer.bin")

    print("******************************************")
print(__file__)
print("***********************************************************************************")
print('best: ', best_equacc, best_vacc, tt, best_vacc / tt)
print("***********************************************************************************")
