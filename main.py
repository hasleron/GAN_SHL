# -*- coding: utf-8 -*-
from hyperopt import hp, fmin, tpe, Trials, space_eval
from hyperopt.pyll import scope
from math import inf as INF
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import confusion_matrix
import torch

plt.ioff()

# from sklearn.exceptions import DataConversionWarning

# # Raise exception for warnings
# import warnings
# warnings.filterwarnings('error', category=DataConversionWarning)

if __package__ is None or __package__ == '':
    import data_source as ds
    import GAN
    from metrics import calc_accuracy, calc_f1score
    from params import Params, save_fig, save_trials, load_trials
    from plot_confusion_matrix import plot_confusion_matrix
    import preprocessing as pp
else:
    from . import data_source as ds
    from . import GAN
    from .metrics import calc_accuracy, calc_f1score
    from .params import Params, save_fig, save_trials, load_trials
    from .plot_confusion_matrix import plot_confusion_matrix
    from . import preprocessing as pp


def pytorch_baseline(P):
    import torch
    import network
    
    #P.set('CUDA',False)
    P.set('C_aco_func','gumbel')
   
    DL_L, DL_U_iter, DL_V = pp.get_all_dataloader(P, ds.get_data(P)) 
    
    input_size, output_size = P.get_IO_shape()
    C = network.new_C(P,input_size=input_size,hidden_size=P.get('C_hidden'),num_classes=output_size)
    C_Loss = network.CrossEntropyLoss_OneHot()
    
    
    optim = 'Adam'
    
    if optim == 'Adam':
        optimizer_C = torch.optim.Adam(C.parameters(), lr=0.001, betas=(0.9,0.999))
    elif optim == 'AdamW':
        optimizer_C = torch.optim.AdamW(C.parameters(), lr=0.001, betas=(0.9,0.999))
    elif optim == 'SGD':
        optimizer_C = torch.optim.SGD(C.parameters(), lr=0.001, momentum=0.9)
        
    if P.get('CUDA') and torch.cuda.is_available():
        device = torch.device('cuda')
        C.cuda()
        C_Loss.cuda()
        P.log("Cuda Training")
    else:
        device = torch.device('cpu')
        P.log("CPU Training")
    
    for epoch in range(200):
        running_loss_C = 0.0
        C.train()
        for i, (X1, Y1) in enumerate(DL_L, 1):
            optimizer_C.zero_grad()
            P1 = C(X1)
            loss = C_Loss(P1, Y1)
            loss.backward()
            optimizer_C.step()
            running_loss_C += loss.item()
        loss_C = running_loss_C/len(DL_L) 
        with torch.no_grad():
            acc_C_G = np.mean([calc_accuracy(C(XV), YV) for (XV, YV) in DL_V])
            C.eval()
            acc_C_S = np.mean([calc_accuracy(C(XV), YV) for (XV, YV) in DL_V])
        P.log(f"Epoch {epoch+1}: Loss = {loss_C:.4f} | Accuracy Gumbel = {acc_C_G:.4f} | Accuracy Softmax = {acc_C_S:.4f}")

   
def sklearn_baseline(P):
    from sklearn.neural_network import MLPClassifier as MLP
    from sklearn.ensemble import RandomForestClassifier

    F = pp.perform_preprocessing(P, ds.get_data(P))
    
    x_train, y_train = F[0]
    x_test, y_test = F[2]
    
    mlp = MLP(hidden_layer_sizes=(100,100),max_iter=500)
    
    mlp.fit(x_train, y_train.ravel())
    
    score = mlp.score(x_train, y_train.ravel())
    P.log(f"MLP Acc Train: {score:.2f}")
    
    score = mlp.score(x_test, y_test.ravel())
    P.log(f"MLP Acc Test: {score:.2f}")
    
    rfc = RandomForestClassifier()
    
    rfc.fit(x_train, y_train.ravel())
    
    score = rfc.score(x_train, y_train.ravel())
    P.log(f"RFC Acc Train: {score:.2f}")
    
    score = rfc.score(x_test, y_test.ravel())
    P.log(f"RFC Acc Test: {score:.2f}")


def hyperopt_Search(P,param_space,objective_func,eval_step=5,max_evals=None):
    P.log("Params: "+str(P),name='hyperopt')
    
    if P.get('CUDA') and torch.cuda.is_available():
        P.log("CUDA Training.",name='hyperopt')
    else:
        P.log("CPU Training.",name='hyperopt')
 
    trials = load_trials(P)
    if trials is None:
        trials = Trials()
    
    while True:
        if max_evals is not None:
            if len(trials.trials) >= max_evals:
                P.log(f"Maximum number of evaluations reached ({len(trials.trials)}/{max_evals})",name='hyperopt')
                try:
                    best_param = fmin(objective_func, param_space, algo=tpe.suggest, max_evals=len(trials.trials), trials=trials, rstate=np.random.RandomState(42))
                    P.log(f"Best Performance: {abs(min(trials.losses())):.5f} - Copy Params: "+", ".join([(key if key[0] != 'C' else 'R'+key[1:])+' = '+ ("'"+val+"'" if isinstance(val,str) else str(val)) for key,val in space_eval(param_space, best_param).items()]),name='hyperopt')
                except:
                    P.log("Couldn't log best performance.")
                break
            evals = min(max_evals,len(trials.trials) + eval_step)
        else:
            evals = len(trials.trials) + eval_step
        evals = max(25,evals)
        
        best_param = fmin(objective_func, param_space, algo=tpe.suggest, max_evals=evals, trials=trials, rstate=np.random.RandomState(42))
        save_trials(P,trials)
        P.log("Best Params:",name='hyperopt')
        for key,val in space_eval(param_space, best_param).items():
            P.log(str(key)+': '+str(val),name='hyperopt')
        P.log(f"Best Performance: {abs(min(trials.losses())):.5f} - Copy Params: "+" ".join([key+' = '+ ("'"+val+"'" if isinstance(val,str) else str(val))+',' for key,val in space_eval(param_space, best_param).items()]),name='hyperopt')   


def hyperopt_GD(P,eval_step=5,max_evals=None):
    param_space= {
        'FX_num'          : scope.int(hp.quniform('FX_num', 200, 500, q=1)),
        
        'GLR'             : hp.loguniform('GLR', np.log(0.00001), np.log(0.1)),
        'GB1'             : hp.loguniform('GB1', np.log(0.001), np.log(0.99)),
        'DLR'             : hp.loguniform('DLR', np.log(0.00001), np.log(0.1)),
        'DB1'             : hp.loguniform('DB1', np.log(0.001), np.log(0.99)),

        
        'G_ac_func'       : hp.choice('G_ac_func',['relu','leaky','leaky20','sig']),
        'G_hidden'        : scope.int(hp.qloguniform('G_hidden', np.log(16), np.log(4096), q=1)),
        'G_hidden_no'     : scope.int(hp.quniform('G_hidden_no', 0, 9, q=1)), 
        'G_optim'         : hp.choice('G_optim',['AdamW','SGD']),
        
        'D_ac_func'       : hp.choice('D_ac_func',['relu','leaky','leaky20','sig']),
        'D_hidden'        : scope.int(hp.qloguniform('D_hidden', np.log(16), np.log(4096), q=1)),
        'D_hidden_no'     : scope.int(hp.quniform('D_hidden_no', 0, 9, q=1)), 
        'D_optim'         : hp.choice('D_optim',['AdamW','SGD']),
    }
    
    F = ds.get_data(P)
    P.log("Data loaded.")
    
    def obj(args):
        P0 = P.copy()
        P0.update(args)
        P0.set('save_step',P0.get('epochs_GD'))
        P0.log("Check Params: "+", ".join([str(key)+' = '+ ("'"+val+"'" if isinstance(val,str) else str(val)) for key,val in args.items()]),name='hyperopt')

        DL_L, DL_U_iter, DL_V = pp.get_all_dataloader(P0, ds.select_features(F,P0.get('FX_indeces')))
        P0.log(f"Number of batches: Labelled = {len(DL_L)} | Unlabelled = {len(DL_U_iter)} | Validation = {len(DL_V)}")
            
        perf_mat = np.empty(shape=(4,P.get('runs')))
        for run in range(P0.get('runs')):
            G, D, mat_accuracy, mat_f1_score = GAN.train_GD(P0, DL_L, DL_U_iter, DL_V, name=P0.get('name')+'_%d'%run,print_savestep=False)

            perf_mat[0,run] = mat_accuracy[0,-1]
            perf_mat[1,run] = mat_accuracy[1,-1]
            perf_mat[2,run] = mat_f1_score[0,-1]
            perf_mat[3,run] = mat_f1_score[1,-1] 
            
        perf = np.mean(perf_mat,axis=1)
        val = sum((0.5-min(0.5,x))**2 for x in perf)
        P0.log(f"loss = {val} [F1: G = {perf[2]:.5f} D = {perf[3]:.5f}] [Accuracy: G = {perf[0]:.5f} D = {perf[1]:.5f}]",name='hyperopt')
        return val
 
    hyperopt_Search(P,param_space,obj,eval_step=eval_step,max_evals=max_evals)
    

def hyperopt_R(P,eval_step=5,max_evals=None): 
    P.set('save_step',INF)
    
    param_space= {
        'epochs'          : scope.int(hp.uniform('epochs',25,200)),
        'batch_size'      : scope.int(hp.qloguniform('batch_size', np.log(256), np.log(1024), q=1)),
        'FX_num'          : scope.int(hp.quniform('FX_num', 1, 500, q=1)),

        'RLR'             : hp.loguniform('RLR', np.log(0.00001), np.log(0.1)),
        'RB1'             : hp.loguniform('RB1', np.log(0.001), np.log(0.99)),
        'R_tau'           : hp.loguniform('R_tau', np.log(0.01), np.log(10.)),
        
        
        'R_ac_func'       : hp.choice('R_ac_func',['relu','leaky','leaky20','sig']),
        'R_aco_func'      : hp.choice('R_aco_func',['gumbel','softmax']),
        'R_hidden'        : scope.int(hp.qloguniform('R_hidden', np.log(16), np.log(4096), q=1)),
        'R_hidden_no'     : scope.int(hp.quniform('R_hidden_no', 0, 9, q=1)), 
        'R_optim'         : hp.choice('R_optim',['Adam','AdamW','SGD']),
    }
    
    if P.get('CUDA') and torch.cuda.is_available():
        P.log("Cuda Training")
    else:
        P.log("CPU Training")
    
    F = ds.get_data(P)
    P.log("Data loaded.")
    
    def obj(args):
        P0 = P.copy()
        P0.update(args)
        P0.log("Check Params: "+", ".join([str(key)+' = '+ ("'"+val+"'" if isinstance(val,str) else str(val)) for key,val in args.items()]),name='hyperopt')
        DL_L, DL_U_iter, DL_V = pp.get_all_dataloader(P0, ds.select_features(F,P0.get('FX_indeces')))
        
        perf_mat = np.empty(shape=(2,P.get('runs'),len(DL_V)))
        for run in range(P0.get('runs')):

            C, _, _ = GAN.train_Base(P0, DL_L, DL_U_iter, DL_V, name=P0.get('name')+'_%d'%run)
            C.eval()
            with torch.no_grad():
                for i,(XV, YV) in enumerate(DL_V):
                    perf_mat[0,run,i] = calc_f1score(C(XV), YV)
                    perf_mat[1,run,i] = calc_accuracy(C(XV), YV)
              
        perf = np.mean(perf_mat.reshape(2,-1),axis=1)
        P0.log(f"F1: {perf[0]:.5f} | Accuracy: {perf[1]:.5f}",name='hyperopt')
        return -perf[0]
    
    hyperopt_Search(P,param_space,obj,eval_step=eval_step,max_evals=max_evals)  
    
    
def hyperopt_GAN(P,eval_step=5,max_evals=None):
    P.set('save_step',INF)
    P.set('R_active',False)
    
    
    param_space= {
        'FX_num'          : scope.int(hp.quniform('FX_num', 200, 500, q=1)),
        'epochs_GD'       : scope.int(hp.quniform('epochs_GD', 0, 100, q=1)),
        
        'GLR'             : hp.loguniform('GLR', np.log(0.00001), np.log(0.1)),
        'GB1'             : hp.loguniform('GB1', np.log(0.001), np.log(0.99)),
        'DLR'             : hp.loguniform('DLR', np.log(0.00001), np.log(0.1)),
        'DB1'             : hp.loguniform('DB1', np.log(0.001), np.log(0.99)),
        'CLR'             : hp.loguniform('CLR', np.log(0.00001), np.log(0.1)),
        'CB1'             : hp.loguniform('CB1', np.log(0.001), np.log(0.99)),
        
        'G_ac_func'       : hp.choice('G_ac_func',['relu','leaky','leaky20','sig']),
        'G_hidden'        : scope.int(hp.qloguniform('G_hidden', np.log(16), np.log(4096), q=1)),
        'G_hidden_no'     : scope.int(hp.quniform('G_hidden_no', 0, 9, q=1)), 
        'G_optim'         : hp.choice('G_optim',['AdamW','SGD']),
        
        'D_ac_func'       : hp.choice('D_ac_func',['relu','leaky','leaky20','sig']),
        'D_hidden'        : scope.int(hp.qloguniform('D_hidden', np.log(16), np.log(4096), q=1)),
        'D_hidden_no'     : scope.int(hp.quniform('D_hidden_no', 0, 9, q=1)), 
        'D_optim'         : hp.choice('D_optim',['AdamW','SGD']),
        
        'C_ac_func'       : hp.choice('C_ac_func',['relu','leaky','leaky20','sig']),
        'C_hidden'        : scope.int(hp.qloguniform('C_hidden', np.log(16), np.log(4096), q=1)),
        'C_hidden_no'     : scope.int(hp.quniform('C_hidden_no', 0, 9, q=1)), 
        'C_optim'         : hp.choice('C_optim',['AdamW','SGD']),
        'C_tau'           : hp.loguniform('C_tau', np.log(0.01), np.log(10.)),
    }
    
    F = ds.get_data(P)
    P.log("Data loaded.")
    
    def obj(args):
        P0 = P.copy()
        P0.update(args)
        P0.log("Check Params: "+", ".join([str(key)+' = '+ ("'"+val+"'" if isinstance(val,str) else str(val)) for key,val in args.items()]),name='hyperopt')

        DL_L, DL_U_iter, DL_V = pp.get_all_dataloader(P0, ds.select_features(F,P0.get('FX_indeces')))
        P0.log(f"Number of batches: Labelled = {len(DL_L)} | Unlabelled = {len(DL_U_iter)} | Validation = {len(DL_V)}")
            
        perf_mat = np.empty(shape=(2,P.get('runs'),len(DL_V)))
        for run in range(P0.get('runs')):
            _, _, C, _, _ = GAN.train_GAN(P0, DL_L, DL_U_iter, DL_V, name=P0.get('name')+'_%d'%run)
            C.eval()
            with torch.no_grad():
                for i,(XV, YV) in enumerate(DL_V):
                    perf_mat[0,run,i] = calc_f1score(C(XV), YV)
                    perf_mat[1,run,i] = calc_accuracy(C(XV), YV)
              
        perf = np.mean(perf_mat.reshape(2,-1),axis=1)
        P0.log(f"F1: {perf[0]:.5f} | Accuracy: {perf[1]:.5f}",name='hyperopt')
        return -perf[0]
 
    hyperopt_Search(P,param_space,obj,eval_step=eval_step,max_evals=max_evals)

    
def get_Results(P,P_val=None):
    P.log("Params: "+str(P))
    
    if P.get('CUDA') and torch.cuda.is_available():
        P.log("CUDA Training.")
    else:
        P.log("CPU Training.")
    
    DL_L, DL_U_iter, DL_V = pp.get_all_dataloader(P, ds.get_data(P))
    P.log(f"Number of batches: Labelled = {len(DL_L)} | Unlabelled = {len(DL_U_iter)} | Validation = {len(DL_V)}")
    
    if P_val is not None:
        P.log("Load Validation data.")
        _, _, DL_V = pp.get_all_dataloader(P_val, ds.get_data(P_val))
    
    ACC = None
    F1S = None
    YF = None
    RF = None
    PF = None
    
    # -------------------
    #  Individual runs
    # -------------------
    
    for run in range(P.get('runs')):
    
        G, D, C, mat_accuracy, mat_f1_score = GAN.train_GAN(P, DL_L, DL_U_iter, DL_V, name=P.get('name')+'_%d'%run)
        
        if P.get('R_active'):
            R, acc_BASE, f1_BASE = GAN.train_Base(P, DL_L, DL_U_iter, DL_V, name=P.get('name')+'_%d'%run)
            mat_accuracy = np.concatenate((mat_accuracy,acc_BASE))
            mat_f1_score = np.concatenate((mat_f1_score,f1_BASE))
        if ACC is None:
            ACC = np.expand_dims(mat_accuracy,axis=2)
            F1S = np.expand_dims(mat_f1_score,axis=2)
        else:
            ACC = np.concatenate((ACC, np.expand_dims(mat_accuracy,axis=2)),axis=2)
            F1S = np.concatenate((F1S, np.expand_dims(mat_accuracy,axis=2)),axis=2)
            
        C.eval()
        if P.get('R_active'):
            R.eval()
            
        with torch.no_grad():
            for XV, YV in DL_V:
                
                # Classify Validation data
                PC = C(XV)
                
                if YF == None:
                    YF = YV
                    PF = PC
                else:
                    YF = torch.cat((YF, YV), 0)
                    PF = torch.cat((PF, PC), 0)
                    
                if P.get('R_active'):
                    if RF == None:
                        RF = R(XV)
                    else:
                        RF = torch.cat((RF, R(XV).detach()), 0)
        
    return ACC, F1S, (YF, RF, PF)

def evaluate(P,P_val=None):
    P.set('R_active',True)
    ACC, F1S, (YF, RF, PF) = get_Results(P,P_val)

    # -------------------
    #  Plot Accuracy
    # -------------------
    
    timeline = np.arange(0,(P.get('epochs_GD')+P.get('epochs'))+1,P.get('save_step'))
    
    def get_label(name,model):
        if name == "Accuracy": return "Accuracy $A_%s$"%model;
        elif name == "F1 Score": return "F1 Score $F_%s$"%model;
        else: return "NO_NAME"+model
        
    for name,mat in (('Accuracy',ACC),("F1 Score",F1S)):
    
        mean_G = np.mean(mat[0],axis=1)
        std_G = np.std(mat[0],axis=1)
        mean_D = np.mean(mat[1],axis=1)
        std_D = np.std(mat[1],axis=1)
        mean_C = np.mean(mat[2],axis=1)
        std_C = np.std(mat[2],axis=1)
        mean_R = np.mean(mat[3],axis=1)

        plt.figure(figsize=(27,9),dpi=300,clear=True)
        fig, ax = plt.subplots()    
         
        cmap = plt.get_cmap('gnuplot')
        indices = np.linspace(0, cmap.N, 7)
        colors = [cmap(int(i)) for i in indices]
    
        ax.plot(timeline,mean_C,c=colors[0],linestyle='solid',label=get_label(name,'C'))
        ax.fill_between(timeline, mean_C-std_C, mean_C+std_C, alpha=0.3, facecolor=colors[0])
        
        ax.plot(timeline,mean_D,c=colors[1],linestyle='dashed',label=get_label(name,'D'))
        ax.fill_between(timeline, mean_D-std_D, mean_D+std_D, alpha=0.3, facecolor=colors[1])
        
        ax.plot(timeline,mean_G,c=colors[2],linestyle='dotted',label=get_label(name,'G'))
        ax.fill_between(timeline, mean_G-std_G, mean_G+std_G, alpha=0.3, facecolor=colors[2])
        
        Y_max = 1.15
        ax.plot(timeline,mean_R,c=colors[3],linestyle='dashdot',label=get_label(name,'R'))
        
        # perf = np.zeros_like(mean_C)
        # perf[0] = 0.0
        # perf[1:] = (mean_C[1:]-mean_R[1:])/mean_R[1:]
    
        # ax.plot(timeline,perf+1,c=colors[4],linestyle='solid')
        # legend.append("Performance $P_C$")
        
        ax.set_xlim(0.0,(P.get('epochs_GD')+P.get('epochs')))
        ax.set_ylim(0.0,Y_max)
        
        # ax.legend(legend,fontsize=20)
        # ax.set_xlabel('Epoch',fontsize=20)
        # ax.set_ylabel('Accuracy',fontsize=20)
        
        ax.legend()
        ax.set_xlabel('Epoch')
        ax.set_ylabel(name)
            
        ax.grid()
        save_fig(P,'eval_'+('acc' if name == 'Accuracy' else 'f1'),fig)
  
    YF = pp.one_hot_to_labels(P,YF)
    RF = pp.one_hot_to_labels(P,RF)
    PF = pp.one_hot_to_labels(P,PF)

    for Y, name in [(PF,'C'),(RF,'R')]:
        con_mat = confusion_matrix(YF, Y, labels=None, sample_weight=None, normalize=None)
        plot_confusion_matrix(np.divide(con_mat,P.get('runs')).round().astype(int),P,name=name,title='Confusion matrix',fmt='d')
        
        con_mat = confusion_matrix(YF, Y, labels=None, sample_weight=None, normalize='all')
        plot_confusion_matrix(con_mat,P,name=name+'_normalised',title='Confusion matrix',fmt='0.3f')

   
def mrmr(K=None,log=True):
    import pandas as pd
    from sklearn.feature_selection import f_regression
    
    from sliding_window import get_FX_names
    
    if K is None:
        K = 908
    
    P = Params(dataset='SHL',FX_sel='all',cross_val='user')
    F = ds.load_data(P)
    
    X = np.concatenate([X0 for X0,_ in F])
    Y = np.concatenate([Y0 for _,Y0 in F])
   
    X = pd.DataFrame(X, columns = get_FX_names())
    Y = pd.Series(Y.ravel())
    
    F = pd.Series(f_regression(X, Y)[0], index = X.columns)
    corr = pd.DataFrame(.00001, index = X.columns, columns = X.columns)
    
    # initialize list of selected features and list of excluded features
    selected = []
    not_selected = X.columns.to_list()
    
    # repeat K times
    for i in range(K):
      
        # compute (absolute) correlations between the last selected feature and all the (currently) excluded features
        if i > 0:
            last_selected = selected[-1]
            corr.loc[not_selected, last_selected] = X[not_selected].corrwith(X[last_selected]).abs().clip(.00001)
            
        # compute FCQ score for all the (currently) excluded features (this is Formula 2)
        score = F.loc[not_selected] / corr.loc[not_selected, selected].mean(axis = 1).fillna(.00001)
        
        # find best feature, add it to selected and remove it from not_selected
        best = score.index[score.argmax()]
        
        if log:
            P.log(str(i+1).rjust(3,' ')+f': {best} (Score: {score[best]:.4f})')
        selected.append(best)
        not_selected.remove(best)
        
    indeces = [X.columns.get_loc(c) for c in selected]
        
    if log:
        P.log(str(selected))
        P.log(str(indeces))

    return selected, indeces
 
def main():
    import argparse
    from params import DEFAULT_PARAMS as default
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument('-test', dest='TEST', action='store_true')
    parser.set_defaults(TEST=False)
    
    parser.add_argument('-eval', dest='EVAL', action='store_true')
    parser.set_defaults(EVAL=False)
    
    parser.add_argument('-search', dest='SEARCH', action='store_true')
    parser.set_defaults(SEARCH=False)
    
    parser.add_argument('-search_c', dest='SEARCH_C', action='store_true')
    parser.set_defaults(SEARCH_C=False)
    
    parser.add_argument('-search_gd', dest='SEARCH_GD', action='store_true')
    parser.set_defaults(SEARCH_GD=False)
    
    parser.add_argument('-mrmr', dest='MRMR', action='store_true')
    parser.set_defaults(MRMR=False)
    
    parser.add_argument('-cuda', dest='CUDA', action='store_true')
    parser.add_argument('-cpu', dest='CUDA', action='store_false')
    parser.set_defaults(CUDA=default['CUDA'])
    
    parser.add_argument('-data_path', type=str, dest='data_path')
    parser.set_defaults(data_path=default['data_path'])
    
    parser.add_argument('-print', dest='PRINT', action='store_true')
    parser.set_defaults(PRINT=default['print_epoch'])
    
    parser.add_argument('-basic', dest='BASIC', action='store_true')
    parser.add_argument('-no_basic', dest='BASIC', action='store_false')
    parser.set_defaults(BASIC=default['C_basic_train'])
    
    parser.add_argument('-max_evals', type=int, dest='max_evals')
    parser.set_defaults(max_evals=None)
    
    args = parser.parse_args()
    P_args = Params(
        data_path = args.data_path,
        CUDA = args.CUDA,
        print_epoch = args.PRINT,
        C_basic_train = args.BASIC,
        )
    
       
    P_search = P_args.copy()
    P_search.set_keys(
        name = 'Hyper_GAN_3.3',
        dataset = 'SHL',
        data_path = args.data_path,
        CUDA = args.CUDA,

        epochs = 100,
        epochs_GD = 100,
        runs = 5,
        
        batch_size = 512,
        
        FX_sel = 'all',
        Cross_val = 'user',
        
        User_L = 3,
        User_U = 2,
        User_V = 1,
        
        sample_no = None,
        undersampling = True,
        oversampling = False,
        ) 
    
    P_test = P_args.copy()
    P_test.set_keys(
        name = 'Test',
        dataset = 'Test',
        data_path = args.data_path,
        CUDA = args.CUDA,

        epochs = 5,
        epochs_GD = 5,
        save_step = 1,
        runs = 1,
        
        FX_sel = 'all',
        Cross_val = 'user',
        
        sample_no = None,
        undersampling = False,
        oversampling = False,
        
        CB1 = 0.02482259369526197, 
        CLR = 0.00033565485364740803, 
        C_ac_func = 'relu', 
        C_hidden = 92, 
        C_optim = 'AdamW', 
        DB1 = 0.1294935579262613, 
        DLR = 0.010144020667237321, 
        D_ac_func = 'leaky', 
        D_hidden = 317, 
        D_optim = 'AdamW', 
        GB1 = 0.023718651003136713,
        GLR = 0.0005411668775518598, 
        G_ac_func = 'relu', 
        G_hidden = 140, 
        G_optim = 'SGD', 
        batch_size = 110
        ) 
    
    P = P_args.copy()
    P.set_keys(
        name = 'evaluation',
        dataset = 'SHL',
        data_path = args.data_path,
        CUDA = args.CUDA,
        
        #PCA_n_components = 0.85,

        epochs = 2000,
        save_step = 2,
        runs = 3,
        
        FX_sel = 'all',
        
        Cross_val = 'user',
        
        sample_no = None,
        undersampling = False,
        oversampling = False,
        
        User_L = 1,
        User_U = 2,
        User_V = 3,
        
        CB1 = 0.07247356069962284, 
        CLR = 1.631929289680412e-05, 
        C_ac_func = 'leaky20', 
        C_hidden = 697, 
        C_hidden_no = 6, 
        C_optim = 'AdamW', 
        C_tau = 3.962724498941699, 
        
        DB1 = 0.17760140736728408, 
        DLR = 0.0001856573882131211, 
        D_ac_func = 'sig', 
        D_hidden = 32, 
        D_hidden_no = 7, 
        D_optim = 'SGD', 
        
        FX_num = 38, 
        
        GB1 = 0.04999825986171865, 
        GLR = 0.03250081887833738, 
        G_ac_func = 'leaky', 
        G_hidden = 157, 
        G_hidden_no = 4, 
        G_optim = 'SGD', 
        
        batch_size = 256,
        
        RB1 = 0.13905385615810364, 
        RLR = 2.6665447517436225e-05, 
        R_ac_func = 'leaky', 
        R_aco_func = 'softmax', 
        R_hidden = 2719, 
        R_hidden_no = 3, 
        R_optim = 'AdamW', 
        R_tau = 0.606889373892653,
        ) 
    

    if args.TEST:
        P_test.set_keys(CUDA = True,)
        evaluate(P_test)
        hyperopt_GAN(P_test,eval_step=2,max_evals=5)
        P_test.set_keys(CUDA = False,)
        evaluate(P_test)
        hyperopt_GAN(P_test,eval_step=2,max_evals=5)
    
    if args.EVAL:
        P_val = P.copy()
        P_val.set_keys(
            sample_no = None,
            undersampling = False,
            oversampling = False,
            )
        
        for cross_val in ['user','none']:
            for basic_train in [True,False]:
                   P.set_keys(
                        name = '_'.join(['eval','C',('Complete' if basic_train else 'GAN'),cross_val,'cross']),
                        C_basic_train = basic_train,
                        Cross_val = cross_val,
                        )
                   evaluate(P,P_val)

    if args.SEARCH:
        hyperopt_GAN(P_search,eval_step=5,max_evals=args.max_evals)
    
    if args.SEARCH_C:
        P_C = P_search.copy()
        P_C.set_keys(name='Hyperopt_C_1.1')
        hyperopt_R(P_C,eval_step=5,max_evals=args.max_evals)
    
    if args.SEARCH_GD:
        P_GD = P_search.copy()
        P_GD.set_keys(name='Hyperopt_GD_1.0')
        hyperopt_GD(P_GD,eval_step=5,max_evals=args.max_evals)
    
    if args.MRMR:
        mrmr()

    #sklearn_baseline(P)
    #pytorch_baseline(P)
    

if __name__ == "__main__":
    main()