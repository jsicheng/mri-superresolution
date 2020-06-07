import cv2
import numpy as np
import os
import pickle
import sys
import time
from gaussian2d import gaussian2d
from gettestargs import gettestargs
from hashkey import hashkey
from math import floor
from matplotlib import pyplot as plt
from scipy import interpolate
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

args = gettestargs()

# Define parameters
R = 2
patchsize = 11
gradientsize = 9
Qangle = 24
Qstrength = 3
Qcoherence = 3
trainpath = '../lr/{}'.format(R)

# Calculate the margin
maxblocksize = max(patchsize, gradientsize)
margin = floor(maxblocksize/2)
patchmargin = floor(patchsize/2)
gradientmargin = floor(gradientsize/2)

# Read filter from file
filtername = 'filter.p'
if args.filter:
    filtername = args.filter
with open(filtername, "rb") as fp:
    h = pickle.load(fp)

# Matrix preprocessing
# Preprocessing normalized Gaussian matrix W for hashkey calculation
weighting = gaussian2d([gradientsize, gradientsize], 2)
weighting = np.diag(weighting.ravel())

# Get image list
imagelist = []
for parent, dirnames, filenames in os.walk(trainpath):
    for filename in filenames:
        if filename.lower().endswith(('.bmp', '.dib', '.png', '.jpg', '.jpeg', '.pbm', '.pgm', '.ppm', '.tif', '.tiff')):
            imagelist.append(os.path.join(parent, filename))

# record metrics
metricspath = 'results/{}/metrics.txt'.format(os.path.basename(filtername))
os.makedirs(os.path.dirname(metricspath), exist_ok=True)
metrics = open(metricspath, 'w+')

all_timings = []
# all_psnr = []
# all_ssim = []

imagecount = 1
for image in imagelist:
    print('\r', end='')
    print(' ' * 60, end='')
    print('\rUpscaling image ' + str(imagecount) + ' of ' + str(len(imagelist)) + ' (' + image + ')')
    origin = cv2.imread(image)
    starttime = time.time()
    
    # Extract only the luminance in YCbCr
    ycrcvorigin = cv2.cvtColor(origin, cv2.COLOR_BGR2YCrCb)
    grayorigin = ycrcvorigin[:,:,0]
    # Normalized to [0,1]
    grayorigin = cv2.normalize(grayorigin.astype('float'), None, grayorigin.min()/255, grayorigin.max()/255, cv2.NORM_MINMAX)
    # Upscale (bilinear interpolation)
    heightLR, widthLR = grayorigin.shape
    heightgridLR = np.linspace(0,heightLR-1,heightLR)
    widthgridLR = np.linspace(0,widthLR-1,widthLR)
    bilinearinterp = interpolate.interp2d(widthgridLR, heightgridLR, grayorigin, kind='linear')
    heightgridHR = np.linspace(0,heightLR-0.5,heightLR*R)
    widthgridHR = np.linspace(0,widthLR-0.5,widthLR*R)
    upscaledLR = bilinearinterp(widthgridHR, heightgridHR)
    # Calculate predictHR pixels
    heightHR, widthHR = upscaledLR.shape
    predictHR = np.zeros((heightHR-2*margin, widthHR-2*margin))
    operationcount = 0
    totaloperations = (heightHR-2*margin) * (widthHR-2*margin)
    for row in range(margin, heightHR-margin):
        for col in range(margin, widthHR-margin):
            if round(operationcount*100/totaloperations) != round((operationcount+1)*100/totaloperations):
                print('\r|', end='')
                print('#' * round((operationcount+1)*100/totaloperations/2), end='')
                print(' ' * (50 - round((operationcount+1)*100/totaloperations/2)), end='')
                print('|  ' + str(round((operationcount+1)*100/totaloperations)) + '%', end='')
                sys.stdout.flush()
            operationcount += 1
            # Get patch
            patch = upscaledLR[row-patchmargin:row+patchmargin+1, col-patchmargin:col+patchmargin+1]
            patch = patch.ravel()
            # Get gradient block
            gradientblock = upscaledLR[row-gradientmargin:row+gradientmargin+1, col-gradientmargin:col+gradientmargin+1]
            # Calculate hashkey
            gy, gx = np.gradient(gradientblock)
            angle, strength, coherence = hashkey(gy, gx, Qangle, weighting)
            # Get pixel type
            pixeltype = ((row-margin) % R) * R + ((col-margin) % R)
            predictHR[row-margin,col-margin] = patch.dot(h[angle,strength,coherence,pixeltype])
    # Scale back to [0,255]
    predictHR = np.clip(predictHR.astype('float') * 255., 0., 255.)
    # Bilinear interpolation on CbCr field
    result = np.zeros((heightHR, widthHR, 3))
    y = ycrcvorigin[:,:,0]
    bilinearinterp = interpolate.interp2d(widthgridLR, heightgridLR, y, kind='linear')
    result[:,:,0] = bilinearinterp(widthgridHR, heightgridHR)
    cr = ycrcvorigin[:,:,1]
    bilinearinterp = interpolate.interp2d(widthgridLR, heightgridLR, cr, kind='linear')
    result[:,:,1] = bilinearinterp(widthgridHR, heightgridHR)
    cv = ycrcvorigin[:,:,2]
    bilinearinterp = interpolate.interp2d(widthgridLR, heightgridLR, cv, kind='linear')
    result[:,:,2] = bilinearinterp(widthgridHR, heightgridHR)
    result[margin:heightHR-margin,margin:widthHR-margin,0] = predictHR
    result = cv2.cvtColor(np.uint8(result), cv2.COLOR_YCrCb2RGB)
    
    endtime = time.time()
    timedelta = endtime - starttime
    metrics.write(timedelta + '\n')
    
    cv2.imwrite('results/{}/'.format(os.path.basename(filtername)) + os.path.splitext(os.path.basename(image))[0] + '.png', cv2.cvtColor(result, cv2.COLOR_RGB2BGR))

    # hr = cv2.imread('../data/test/{}'.format(os.path.basename(image)))
    # psnr = peak_signal_noise_ratio(hr, result)
    # ssim = structural_similarity(hr, result)
    # all_psnr.append(psnr)
    # all_ssim.append(ssim)
    all_timings.append(timedelta)
    # print('PSNR: {}; SSIM: {}; Time: {}'.format(psnr, ssim, timedelta))
    # metrics.write('PSNR: {}; SSIM: {}; Time: {}\n'.format(psnr, ssim, timedelta))

    imagecount += 1
    # Visualizing the process of RAISR image upscaling
    if args.plot:
        fig = plt.figure()
        ax = fig.add_subplot(1, 4, 1)
        ax.imshow(grayorigin, cmap='gray', interpolation='none')
        ax = fig.add_subplot(1, 4, 2)
        ax.imshow(upscaledLR, cmap='gray', interpolation='none')
        ax = fig.add_subplot(1, 4, 3)
        ax.imshow(predictHR, cmap='gray', interpolation='none')
        ax = fig.add_subplot(1, 4, 4)
        ax.imshow(result, interpolation='none')
        plt.show()

# print('Avg PSNR: {}; Avg SSIM: {}; Total time: {}'.format(np.mean(all_psnr), np.mean(all_ssim), np.sum(all_timings)))
# metrics.write('Avg PSNR: {}; Avg SSIM: {}; Total time: {}\n'.format(np.mean(all_psnr), np.mean(all_ssim), np.sum(all_timings)))
# print(all_psnr, all_ssim, all_timings)
# metrics.write(str(all_psnr) + '\n')
# metrics.write(str(all_ssim) + '\n')
# metrics.write(str(all_timings) + '\n')

print('\r', end='')
print(' ' * 60, end='')
print('\rFinished.')

metrics.close()
