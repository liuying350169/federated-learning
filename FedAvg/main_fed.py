#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Python version: 3.6

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import datetime
import os
import copy
import numpy as np
from torchvision import datasets, transforms
#from tqdm import tqdm
import torch
import torch.nn.functional as F
from torch import autograd
from tensorboardX import SummaryWriter

from sampling import mnist_iid, mnist_noniid, cifar_iid, mnist_noniid_extram, cifar_noniid, cifar_noniid_extram, cifar100_iid, cifar100_noniid, cifar100_noniid_extram
from options import args_parser
from Update import LocalUpdate
from FedNets import MLP, CNNMnist, CNNCifar
from averaging import average_weights


# def test(net_g, data_loader, args):
#     # testing
#     test_loss = 0
#     correct = 0
#     l = len(data_loader)
#     for idx, (data, target) in enumerate(data_loader):
#         if args.gpu != -1:
#             data, target = data.cuda(), target.cuda()
#         data, target = autograd.Variable(data), autograd.Variable(target)
#         log_probs = net_g(data)
#         test_loss += F.nll_loss(log_probs, target, size_average=False).item() # sum up batch loss
#         y_pred = log_probs.data.max(1, keepdim=True)[1] # get the index of the max log-probability
#         correct += y_pred.eq(target.data.view_as(y_pred)).long().cpu().sum()
#
#     test_loss /= len(data_loader.dataset)
#     f = open('./test.txt', 'a')
#     print('\nTest set: Average loss: {:.4f} \nAccuracy: {}/{} ({:.2f}%)\n'.format(
#         test_loss, correct, len(data_loader.dataset),
#         100. * correct / len(data_loader.dataset)),file=f)
#     print('\nTest set: Average loss: {:.4f} \nAccuracy: {}/{} ({:.2f}%)\n'.format(
#         test_loss, correct, len(data_loader.dataset),
#         100. * correct / len(data_loader.dataset)))
#     f.close()
#     return correct, test_loss


if __name__ == '__main__':
    # parse args
    args = args_parser()

    #write args to file
    f = open('./test.txt', 'a')
    print(args,file=f)
    f.close()
    # define paths
    path_project = os.path.abspath('..')

    summary = SummaryWriter('local')

    # load dataset and split users
    if args.dataset == 'mnist':
        dataset_train = datasets.MNIST('../data/mnist/', train=True, download=True,
                   transform=transforms.Compose([
                       transforms.ToTensor(),
                       transforms.Normalize((0.1307,), (0.3081,))
                   ]))
        dataset_test = datasets.MNIST('../data/mnist/', train=False, download=True,
                   transform=transforms.Compose([
                       transforms.ToTensor(),
                       transforms.Normalize((0.1307,), (0.3081,))
                   ]))
        # sample users
        if args.iid == 1:
            dict_users = mnist_iid(dataset_train, args.num_users)
        elif args.iid == 2:
            #return 5 shards and each shard has 12000 imgs
            dict_users = mnist_noniid_extram(dataset_train, args.num_users)
        else:
            dict_users = mnist_noniid(dataset_train, args.num_users)

    elif args.dataset == 'cifar':
        transform = transforms.Compose(
            [transforms.ToTensor(),
             transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
        dataset_train = datasets.CIFAR10('../data/cifar', train=True, transform=transform, target_transform=None, download=True)
        dataset_test = datasets.CIFAR10('../data/cifar', train=False, transform=transform, target_transform=None, download=True)

        if args.iid == 1:
            print("iid==1")
            dict_users = cifar_iid(dataset_train, args.num_users)
        elif args.iid == 2:
            print("iid==2")
            dict_users = cifar_noniid_extram(dataset_train, args.num_users)
        else:
            print("iid==0")
            dict_users = cifar_noniid(dataset_train, args.num_users)

    elif args.dataset == 'cifar100':
        transform = transforms.Compose(
            [transforms.ToTensor(),
             transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
        dataset_train = datasets.CIFAR100('../data/cifar100', train=True, transform=transform, target_transform=None, download=True)
        dataset_test = datasets.CIFAR100('../data/cifar100', train=False, transform=transform, target_transform=None, download=True)

        if args.iid==1:
            dict_users = cifar100_iid(dataset_train, args.num_users)
        elif args.iid==2:
            dict_users = cifar100_noniid_extram(dataset_train, args.num_users)
        else:
            dict_users = cifar100_noniid(dataset_train, args.num_users)

    else:
        exit('Error: unrecognized dataset')
    img_size = dataset_train[0][0].shape



    # build model
    if args.model == 'cnn' and args.dataset == 'cifar':
        if args.gpu != -1:
            torch.cuda.set_device(args.gpu)
            net_glob = CNNCifar(args=args).cuda()
        else:
            net_glob = CNNCifar(args=args)

    elif args.model == 'cnn' and args.dataset == 'cifar100':
        if args.gpu != -1:
            torch.cuda.set_device(args.gpu)
            net_glob = CNNCifar(args=args).cuda()
        else:
            net_glob = CNNCifar(args=args)
    elif args.model == 'cnn' and args.dataset == 'mnist':
        if args.gpu != -1:
            torch.cuda.set_device(args.gpu)
            net_glob = CNNMnist(args=args).cuda()
        else:
            net_glob = CNNMnist(args=args)
    elif args.model == 'mlp':
        len_in = 1
        for x in img_size:
            len_in *= x
        if args.gpu != -1:
            torch.cuda.set_device(args.gpu)
            net_glob = MLP(dim_in=len_in, dim_hidden=64, dim_out=args.num_classes).cuda()
        else:
            net_glob = MLP(dim_in=len_in, dim_hidden=64, dim_out=args.num_classes)
    else:
        exit('Error: unrecognized model')
    print(net_glob)
    net_glob.train()

    # copy weights
    w_glob = net_glob.state_dict()
    #net_glob is the global model
    #state_dict() is the weights of a model

    # training
    loss_train = []
    acc_train = []
    cv_loss, cv_acc = [], []
    val_loss_pre, counter = 0, 0
    net_best = None
    val_acc_list, net_list = [], []
    #tqdm jin du tiao
    #allids = []
    for iter in range(args.epochs):
        w_locals, loss_locals = [], []
        if(args.num_users <= 10):
            m = args.num_users
            #print("**********m=",m)
            idxs_users = np.random.choice(range(args.num_users), m, replace=False)
        else:
            m = max(int(args.frac * args.num_users), 1)
            #print("**********m=", m)
            #m is select how many ready client to use， default is 10
            idxs_users = np.random.choice(range(args.num_users), m, replace=False)

        #for every select users
        #idxs_users is some numbers
        if(args.exchange == 0):
            for idx in idxs_users:
                # print("user num id",idx)
                # allids.append(idx)
                # allids.sort()
                # print(allids)
                #use LocalUpdate to update weight
                #train_test_validate has [] [] []
                local = LocalUpdate(args=args, dataset=dataset_train, testset=dataset_test, idxs=dict_users, i=idx, tb=summary)
                #LocalUpdate initial
                w, loss = local.update_weights(net=copy.deepcopy(net_glob))
                #use global to train
                # w is local model's state_dict(), means the weight of local model
                # loss is the sum(epoch_loss) / len(epoch_loss)

                #w_locals is [], an empty []
                #w_locals save the local weight
                w_locals.append(copy.deepcopy(w))
                #loss_locals is [], an empty []
                #loss_locals save the loss
                loss_locals.append(copy.deepcopy(loss))
            #w_locals and loss_locals return all select idxs_users

        elif(args.exchange == 1):
            for idx in idxs_users:
                local = LocalUpdate(args=args, dataset=dataset_train, testset=dataset_test, idxs=dict_users, i=idx,
                                    tb=summary)
                w, loss = local.exchange_weight(net=copy.deepcopy(net_glob))
                w_locals.append(copy.deepcopy(w))
                loss_locals.append(copy.deepcopy(loss))

        #liuyyyy
        elif(args.exchange == 2):
            for idx in idxs_users:
                local = LocalUpdate(args=args, dataset=dataset_train, testset=dataset_test, idxs=dict_users, i=idx,
                                    tb=summary)
                w, loss = local.exchange_weight(net=copy.deepcopy(net_glob))
                w_locals.append(copy.deepcopy(w))
                loss_locals.append(copy.deepcopy(loss))

        # update global weights
        # average_weights all w_locals in every epoch
        w_glob = average_weights(w_locals)

        # here can use other ways to average

        # copy weight to net_glob
        net_glob.load_state_dict(w_glob)


        # print loss
        # calculate avg_loss
        loss_avg = sum(loss_locals) / len(loss_locals)

        # error
        # should be iter % 10 == 0:
        # now is do it every time
        # I want to show acc every iter
        # it's truly no use
        if args.epochs % 10 == 0:
            #print(args.epochs)
            list_acc, list_loss = [], []
            #model.eval() makes model can be test
            net_glob.eval()
            #for every user?
            #for every users is because in before every test is different, but now they are same
            #so we can use only one to test
            # if(args.alltest == 1):
            #     net_local = LocalUpdate(args=args, dataset=dataset_train, testset=dataset_test, idxs=dict_users, i=0, tb=summary)
            #     acc, loss = net_local.test(net=net_glob)
            #     acc_avg = acc
            #     #loss_avg = loss
            #elif(args.alltest == 0):
            for c in range(args.num_users):
                #test is not according to users, is the same
                net_local = LocalUpdate(args=args, dataset=dataset_train, testset=dataset_test, idxs=dict_users, i=c, tb=summary)
                acc, loss = net_local.test(net=net_glob)
                list_acc.append(acc)
                list_loss.append(loss)
            acc_avg = 100. * sum(list_acc) / len(list_acc)
            f = open('./test.txt', 'a')
            print('\nTrain loss:', loss_avg)
            #print('\nTrain loss:', loss_avg,file=f)
            print("iter:{} | Train loss:{} | average acc: {:.2f}%".format(iter, loss_avg, acc_avg))
            print("iter:{} | Train loss:{} | average acc: {:.2f}%".format(iter, loss_avg, acc_avg), file=f)
            f.close()
        loss_train.append(loss_avg)
        acc_train.append(acc_avg)


    # plot loss curve
    plt.figure()
    plt.plot(range(len(loss_train)), loss_train)
    plt.ylabel('train_loss')
    plt.savefig('../save/loss_fed_{}_{}_{}_C{}_iid{}_{}.png'.format(args.dataset, args.model, args.epochs, args.num_users, args.iid, datetime.datetime.now()))

    # plot acc curve
    plt.figure()
    plt.plot(range(len(acc_train)), acc_train)
    plt.ylabel('train_acc')
    plt.savefig('../save/acc_fed_{}_{}_{}_C{}_iid{}_{}.png'.format(args.dataset, args.model, args.epochs, args.num_users, args.iid, datetime.datetime.now()))


    # testing
    list_acc, list_loss = [], []
    net_glob.eval()
    for c in range(args.num_users):
        net_local = LocalUpdate(args=args, dataset=dataset_train, testset=dataset_test, idxs=dict_users, i=c, tb=summary)
        acc, loss = net_local.test(net=net_glob)
        list_acc.append(acc)
        list_loss.append(loss)

    f = open('./test.txt', 'a')
    print("average acc: {:.2f}%".format(100.*sum(list_acc)/len(list_acc)))
    print("average acc: {:.2f}%".format(100. * sum(list_acc) / len(list_acc)),file=f)
    f.close()

