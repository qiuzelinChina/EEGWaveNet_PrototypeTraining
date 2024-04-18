import os
import time
import logging
import torch
import torch.nn as nn
import torch.optim as optim
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from torch.utils.data import DataLoader
#from scripts.config import *
from nets.CNN_direction import *
from dataloaders.EEGdataset import EEGDataset_wavelet
import torch.nn.functional as F
import random
import numpy as np
import argparse
import time
import pickle
from utils.utils import view_bar,write_log, get_final_accuracy
from utils.get_split_index import get_split_index_new

torch.manual_seed(123)
torch.cuda.manual_seed(123)
torch.cuda.manual_seed_all(123)
torch.backends.cudnn.enabled = True
torch.backends.cudnn.benchmark = True
from utils.cfg import DEVICE, base_path


parser = argparse.ArgumentParser()
parser.add_argument('--model', type=str, default='EEGWaveNet')
parser.add_argument('--batch', type=int, default=10)
parser.add_argument('--prototype', type=int, default=1)
parser.add_argument('--win', type=float, default=1)
parser.add_argument('--stride', type=float, default=0.5)
parser.add_argument('--lr', type=float, default=0.001)
parser.add_argument('--epoch', type=int, default=10)
parser.add_argument('--subject', type=str, default=[1])   # list style str
parser.add_argument('--name', type=str, default='')
parser.add_argument('--strategy', type=int, default=1)
parser.add_argument('--dataset', type=str, default='Das2016')
parser.add_argument('--date', type=str, default='0530')
parser.add_argument('--band', type=str, default='[1,30]')
parser.add_argument('--topo', type=bool, default=True)
args = parser.parse_args()


#####################
assert args.topo == True or args.topo == False
assert args.dataset == 'Das2016' or args.dataset == 'Fug2018' or args.dataset == 'Fug2020'
args.band = eval(args.band)
args.subject = eval(args.subject)
################# result log
timestr = time.strftime("%Y%m%d-%H%M%S")


#####################

EPOCH = args.epoch
#TRAIN_BATCH_SIZE = 20
TRAIN_BATCH_SIZE = args.batch
TEST_BATCH_SIZE = args.batch







def validation(net, criterion, validation_data_loader, val_batch_num,log_path, word='Validation'):
    
    # during testing, we divide the testset equally to two parts. When evaluate the performance on each part, we regard the other one as validation set.
    net.eval()
    test_loss = 0
    num_total_test = 0
    num_correct_test = 0
    validation_data_loader.dataset.mode = 'test'
    for batch_idx, batch_info in enumerate(validation_data_loader):
        eeg = batch_info[0].float().to(device=DEVICE)    
        eeg_init = batch_info[5].float().to(device=DEVICE)
        target_direction = batch_info[3].long().to(device=DEVICE)
        out_prob = net(eeg, eeg_init, 1)      
        loss = F.cross_entropy(out_prob, target_direction)       
        num_correct_test = num_correct_test + float(torch.sum(torch.argmax(out_prob, dim=1)==target_direction))
        num_total_test = num_total_test + eeg.shape[0]        
        test_loss += loss.item() 
        view_bar(word, batch_idx+1, val_batch_num, test_loss/(batch_idx + 1), test_loss/(batch_idx + 1))

    write_log(log_path, ['  ', 'test', '  ', test_loss/(batch_idx + 1), num_correct_test/num_total_test])


    val_loss = 0
    num_total_val = 0
    num_correct_val = 0
    validation_data_loader.dataset.mode = 'val'
    for batch_idx, batch_info in enumerate(validation_data_loader):
        eeg = batch_info[0].float().to(device=DEVICE)    
        eeg_init = batch_info[5].float().to(device=DEVICE)
        target_direction = batch_info[3].long().to(device=DEVICE)
        out_prob = net(eeg, eeg_init, 1)      
        loss = F.cross_entropy(out_prob, target_direction)       
        num_correct_val = num_correct_val + float(torch.sum(torch.argmax(out_prob, dim=1)==target_direction))
        num_total_val = num_total_val + eeg.shape[0]        
        val_loss += loss.item() 
        view_bar(word, batch_idx+1, val_batch_num, val_loss/(batch_idx + 1), val_loss/(batch_idx + 1))

    write_log(log_path, ['  ', 'val', '  ', val_loss/(batch_idx + 1), num_correct_val/num_total_val])

    # return val_loss / (batch_idx+1), decoding_accuracy
    return [num_correct_test / (num_total_test+1e-8), num_correct_val / (num_total_val+1e-8)]


def train(net, train_data_loader, validation_data_loader, test_data_loader, epoch, tr_batch_num, val_batch_num, test_batch_num, log_path):

    val_snr_list = []
    test_snr_list = []
    val_accuracy_list = []
    test_accuracy_list = []
    val_pesq_list = []
    test_pesq_list = []
    val_stoi_list = []
    test_stoi_list = []
    criterion = 1

    optimizer = optim.Adam(filter(lambda p: p.requires_grad, net.parameters()), lr=1e-3)
    
    old_tr_win_loss = 1000
    update_lr_batch_len = 1000
    keep_model_batch_len = 1000
    keeplen = 1000
    tr_batloss, tr_win_loss = 0, 0
    step_num = 0
    
    for i in range(epoch):
        num_total = 0
        num_correct = 0
        tr_batloss = 0
        start_time = time.time()
        tr_loss = 0

        net.train()            
        for batch_idx, batch_info in enumerate(train_data_loader):      

           
            lr = args.lr
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr

            eeg = batch_info[0].float().to(device=DEVICE)

            eeg_init = batch_info[5].float().to(device=DEVICE)

            target_direction = batch_info[3].long().to(device=DEVICE)

            optimizer.zero_grad()
          
            out_prob = net(eeg, eeg_init, 2)

            loss = F.cross_entropy(out_prob, target_direction)
            
            num_correct = num_correct + float(torch.sum(torch.argmax(out_prob, dim=1)==target_direction))
            num_total = num_total + eeg.shape[0]
    
            loss.backward()
            optimizer.step()
            

            tr_loss += loss.item()
            tr_batloss += loss.item()
            tr_win_loss += loss.item()
            
            view_bar('Training  ', batch_idx+1, tr_batch_num, tr_batloss/(batch_idx%keep_model_batch_len+1), tr_loss /(batch_idx+1))

        write_log(log_path, ['  ', 'train', i+1, tr_loss /(batch_idx+1), num_correct/num_total])


        print(f'\n the {i}th epoch training finished:\n')
        # write_log(result_name_train, f'{i+1}    {float((tr_loss /(batch_idx+1)))}')
            
            



        '''''' 

        if i < epoch - 5 and i % 6 == 0:
            test_accuracy = validation(net, criterion, test_data_loader, test_batch_num, log_path, 'testing:')
            print(f'\ntesting finished, accuracy: {test_accuracy}')
        if i > epoch - 10 :
            test_accuracy = validation(net, criterion, test_data_loader, test_batch_num, log_path, 'testing:')
            print(f'\ntesting finished, accuracy: {test_accuracy}')
            test_accuracy_list.append(test_accuracy)
            
    

   



    return get_final_accuracy(test_accuracy_list)

       
              
        
if __name__ == '__main__':
    
    CV = 4

    results_folder = os.path.join(base_path, f'results/Direction-wavelet-{args.dataset}-{args.model}-strategy{args.strategy}-win{float(args.win)}s-prototype{args.prototype}{args.name}-Val')
    if not os.path.exists(results_folder):
        os.makedirs(results_folder)
    formatted_time = time.strftime("%Y%m%d_%H%M", time.localtime(time.time()))
    sub_str = f'{args.subject[0]}' if args.strategy < 3.5 else 'all_sub'
    log_path_sub = os.path.join(results_folder, f"Sub_{sub_str}-{args.dataset}-strategy{args.strategy}-{formatted_time}.csv")
    log_path_all = os.path.join(results_folder, f"{args.dataset}-strategy{args.strategy}.csv")
    write_log(log_path_sub, [f'CV', 'stage', 'epoch', 'loss', 'accuracy'],'w')

    acc_list = []
    trial_list_train, trial_list_test, time_index_list_train, time_index_list_test, sub_list_train, sub_list_test = get_split_index_new(args.dataset, strategy=args.strategy, win_len=args.win, stride=args.stride,sub_id_list=args.subject,cv=CV, wavelet=True)

    for cv in range(CV):
        
        write_log(log_path_sub, [cv+1],'a')
        print('reading test data..........')
        print(trial_list_test[cv])
        test_data_set = EEGDataset_wavelet(args.dataset, sub_list_test[cv], trial_list_test[cv], time_index_list_test[cv], args.win, ['bandpass', [ 1, 50]], if_val=True)
        test_data_loader = DataLoader(test_data_set, batch_size=args.batch, shuffle=False, num_workers = 8)


        print('synthesize training data..........')
        print(trial_list_train[cv])
        train_data_set = EEGDataset_wavelet(args.dataset, sub_list_train[cv], trial_list_train[cv], time_index_list_train[cv], args.win, ['bandpass', [1, 50]], prototype=args.prototype)
        train_data_loader = DataLoader(train_data_set, batch_size=args.batch, shuffle=True, num_workers = 8)

        tr_batch_num = train_data_set.__len__() // args.batch
        test_batch_num = test_data_set.__len__() // args.batch
        
        print("train batch numbers: %d, test batch numbers: %d" % (tr_batch_num, test_batch_num))
        
        
        model = eval(f'{args.model}({int(args.win*128)})')
        if DEVICE == 'cuda':
            model = torch.nn.DataParallel(model)  # use multiple GPU 
        model = model.to(device=DEVICE)

        total_params = sum(p.numel() for p in model.parameters())
        print('total parameters number:', total_params)
        total_trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        print('total trainable parameters number:', total_trainable_params/1000000)
                


        test_acc = train(model, train_data_loader, None,test_data_loader, EPOCH , tr_batch_num, None, test_batch_num, log_path_sub)

        acc_list.append(test_acc)
        print(f'\nsub: {args.subject}   cv: {cv+1}   avg_acc_now: {sum(acc_list) / len(acc_list)}\n')
    write_log(log_path_all, [f'Sub:{args.subject}']+acc_list+['avg', sum(acc_list) / len(acc_list)],'a')

    acc_list = np.mean(np.array(acc_list), 0)

  
    
    


    