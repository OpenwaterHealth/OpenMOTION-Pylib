import math, os, re, sys, copy
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

def GetHistogramStats(hist, bins, noisyBinMin):
    hist = copy.deepcopy(hist)
    binsSq = np.multiply(bins,bins)
    if hist.ndim==2:
        mean = np.zeros(hist.shape[0])
        std  = np.zeros(hist.shape[0])
        histWid = np.zeros(hist.shape[0])
        for i in range(hist.shape[0]):
            hist[i][hist[i]<noisyBinMin] = 0
            mean[i] = np.dot(hist[i],bins)/np.sum(hist[i])
            var = (np.dot(hist[i],binsSq)-mean[i]*mean[i]*np.sum(hist[i]))/(np.sum(hist[i])-1)
            std[i] = np.sqrt(var)
            histWid[i] = np.sum(hist[i]>100)
    else:
        hist[hist<noisyBinMin] = 0
        mean = np.dot(hist,bins)/np.sum(hist)
        var = (np.dot(hist,binsSq)-mean*mean*np.sum(hist))/(np.sum(hist)-1)
        std = np.sqrt(var)
        histWid = np.sum(hist>100)
    return mean, std, histWid

def GetContrast(mean_laserOn, std_laserOn, mean_laserOff, std_laserOff, ADCgain, cameraGain):
    correctedMean = mean_laserOn - mean_laserOff.mean()
    if np.any(correctedMean<0):
        print('Negative correctedMean in data')
    varCorrected = std_laserOn**2-std_laserOff.mean()**2-ADCgain*cameraGain*correctedMean
    if np.any(varCorrected<0):
        varCorrected = std_laserOn**2
        print('Negative variance in data with correction. Turning off variance correction')
    contrast = np.sqrt(varCorrected)/correctedMean
    return correctedMean, contrast

#%%

direct = ''
histos_laser1x = np.array(pd.read_csv(direct + 'histo_data.csv'))[:,1:-1]
histos_dark1x = np.array(pd.read_csv(direct + 'data_captures/dark_histo_data_352_8.csv'))[:,1:-1]

histos_laser1x[:,0] -= 6
histos_dark1x[:,0] -= 6

fig, ax = plt.subplots(figsize=(8,6), layout='constrained')
x = np.arange(0,1024,1)
ax.semilogy(x, histos_dark1x.mean(axis=0),color='k')
ax.semilogy(x, histos_laser1x.mean(axis=0),color='r')
noisyBinMin = 100
pedHeight = 64
ADCgain = (1024 - pedHeight) / 11000  # photons/electrons = 11000e- / (1024 - dark_level_target)
cameraGain = 16
bins = np.array(list(range(histos_laser1x.shape[1]-1)))
histLsrOffMean, histLsrOffStd, histLsrOffHistWid = GetHistogramStats(histos_dark1x[2:,:-1],bins,noisyBinMin)
histLsrOnMean, histLsrOnStd, histLsrOnHistWid = GetHistogramStats(histos_laser1x[2:,:-1],bins,noisyBinMin)
correctedMean, contrast = GetContrast(histLsrOnMean, histLsrOnStd, histLsrOffMean, histLsrOffStd, ADCgain, cameraGain)

# Various stats output
print(correctedMean.mean(), contrast.mean(), histLsrOnStd.mean(), histLsrOnHistWid.mean())
print(histLsrOffStd.mean(), histLsrOffHistWid.mean())

# CoV for contrast and image mean
print('CoV Mean',correctedMean[4:].std()/correctedMean[4:].mean(), 'CoV Contrast',contrast[4:].std()/contrast[4:].mean())

ax.legend(['Dark 1x','Laser 1x (cont: %.3f, mean: %.1f)' % (contrast.mean(), correctedMean.mean())])
ax.set_xlabel('Digital Number')
ax.set_ylabel('Bin Count')
ax.set_xlim(-5,1028)
ax.set_ylim(0.1,1E6)
ax.grid()
# fig.suptitle('1920 x 1280 = 2457600\n Average sum of dark bins: ' + str(int(histos_dark.mean(axis=0).sum())) +
#              '\n Average sum of laser bins: ' + str(int(histos_laser.mean(axis=0).sum())))
# fig.suptitle(direct.split('/')[-2])
# fig.savefig(direct + 'Averaged Histograms.png', dpi=300)

fig, ax = plt.subplots(figsize=(8,6),nrows=2, layout='constrained')
x = np.arange(0,1024,1)
ax[0].semilogy(x, histos_dark1x.T)
ax[1].semilogy(x, histos_laser1x.T)
ax[0].set_xlim(-5,1028)
ax[1].set_xlim(-5,1028)
ax[1].set_xlabel('Digital Number')
ax[0].set_ylabel('Bin Count')
ax[1].set_ylabel('Bin Count')
ax[0].set_title('Dark Histos')
ax[1].set_title('Laser Histos')
# fig.suptitle(direct.split('/')[-2])
#fig.savefig(direct + 'All Histograms 1x.png', dpi=300)

fig, ax = plt.subplots(figsize=(8,6), layout='constrained')
plt.plot(correctedMean,'b')
plt.ylabel('Corrected Image Mean',color='b')
ax2 = plt.twinx()
ax2.plot(contrast,'r')
ax2.set_ylabel('Contrast',color='r')
plt.grid()
plt.show()