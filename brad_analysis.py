#%%
import math, os, re, sys, copy
import matplotlib.pyplot as plt
#import miscFcns_BH as fcns
import numpy as np
import pandas as pd
#from scipy import signal

#%%

csv_loc = 'histo_data.csv'
histos_laser = pd.read_csv('histo_data.csv')
histos_laser = np.array(histos_laser)

histos_dark = pd.read_csv('data_captures/dark_histo_data_16.csv')
histos_dark = np.array(histos_dark)

max_dim = 1000

fig, ax = plt.subplots(figsize=(8,6))
x = np.arange(0,1026,1)
ax.semilogy(x, histos_dark.mean(axis=0),color='k')
ax.semilogy(x, histos_laser.mean(axis=0),color='r')
ax.legend(['Dark','Laser'])
ax.set_xlabel('Digital Number')
ax.set_ylabel('Bin Count')
ax.set_xlim(0,max_dim)
fig.suptitle('1920 x 1280 = 2457600\n Average sum of dark bins: ' + str(int(histos_dark.mean(axis=0).sum())) +
             '\n Average sum of laser bins: ' + str(int(histos_laser.mean(axis=0).sum())))
fig.savefig('Averaged Histograms.png', dpi=300)

fig, ax = plt.subplots(figsize=(8,6),nrows=2)
x = np.arange(0,1026,1)
ax[0].semilogy(x, histos_dark.T)
ax[1].semilogy(x, histos_laser.T)
ax[0].set_xlim(0,max_dim)
ax[1].set_xlim(0,max_dim)
ax[1].set_xlabel('Digital Number')
ax[0].set_ylabel('Bin Count')
ax[1].set_ylabel('Bin Count')
ax[0].set_title('Dark Histos')
ax[1].set_title('Laser Histos')
fig.savefig('All Histograms.png', dpi=300)