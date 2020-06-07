from __future__ import print_function
from math import log10

import torch
import torch.nn as nn
import torch.optim as optim
import torch.backends.cudnn as cudnn
from torchvision.models.vgg import vgg16
from SRGAN.model import Generator, Discriminator
from progress_bar import progress_bar
from numpy import argmax
from shutil import copyfile

class SRGANTrainer(object):
    def __init__(self, config, training_loader, testing_loader):
        super(SRGANTrainer, self).__init__()
        self.GPU_IN_USE = torch.cuda.is_available()
        self.device = torch.device('cuda' if self.GPU_IN_USE else 'cpu')
        self.netG = None
        self.netD = None
        self.lr = config.lr
        self.nEpochs = config.nEpochs
        self.epoch_pretrain = 10
        self.criterionG = None
        self.criterionD = None
        self.optimizerG = None
        self.optimizerD = None
        self.feature_extractor = None
        self.scheduler = None
        self.seed = config.seed
        self.upscale_factor = config.upscale_factor
        self.num_residuals = 16
        self.training_loader = training_loader
        self.testing_loader = testing_loader

        self.load = config.load
        self.model_path = 'models/SRGAN/' + str(self.upscale_factor)

    def build_model(self):
        self.netG = Generator(n_residual_blocks=self.num_residuals, upsample_factor=self.upscale_factor, base_filter=64, num_channel=1).to(self.device)
        self.netD = Discriminator(base_filter=64, num_channel=1).to(self.device)
        self.feature_extractor = vgg16(pretrained=True)
        self.netG.weight_init(mean=0.0, std=0.2)
        self.netD.weight_init(mean=0.0, std=0.2)
        self.criterionG = nn.MSELoss()
        self.criterionD = nn.BCELoss()
        torch.manual_seed(self.seed)

        if self.GPU_IN_USE:
            torch.cuda.manual_seed(self.seed)
            self.feature_extractor.cuda()
            cudnn.benchmark = True
            self.criterionG.cuda()
            self.criterionD.cuda()

        self.optimizerG = optim.Adam(self.netG.parameters(), lr=self.lr, betas=(0.9, 0.999))
        self.optimizerD = optim.SGD(self.netD.parameters(), lr=self.lr / 100, momentum=0.9, nesterov=True)
        self.scheduler = optim.lr_scheduler.MultiStepLR(self.optimizerG, milestones=[50, 75, 100], gamma=0.5)  # lr decay
        self.scheduler = optim.lr_scheduler.MultiStepLR(self.optimizerD, milestones=[50, 75, 100], gamma=0.5)  # lr decay

    @staticmethod
    def to_data(x):
        if torch.cuda.is_available():
            x = x.cpu()
        return x.data

    def save(self, epoch):
        g_model_out_path = self.model_path + "/g_model_{}.pth".format(epoch)
        d_model_out_path = self.model_path + "/d_model_{}.pth".format(epoch)
        torch.save(self.netG, g_model_out_path)
        torch.save(self.netD, d_model_out_path)
        print("Checkpoint saved to {}".format(g_model_out_path))
        print("Checkpoint saved to {}".format(d_model_out_path))

    def pretrain(self):
        self.netG.train()
        for batch_num, (data, target) in enumerate(self.training_loader):
            data, target = data.to(self.device), target.to(self.device)
            self.netG.zero_grad()
            loss = self.criterionG(self.netG(data), target)
            loss.backward()
            self.optimizerG.step()

    def train(self):
        # models setup
        self.netG.train()
        self.netD.train()
        g_train_loss = 0
        d_train_loss = 0
        for batch_num, (data, target) in enumerate(self.training_loader):
            # setup noise
            real_label = torch.ones(data.size(0), data.size(1)).to(self.device)
            fake_label = torch.zeros(data.size(0), data.size(1)).to(self.device)
            data, target = data.to(self.device), target.to(self.device)

            # Train Discriminator
            self.optimizerD.zero_grad()
            d_real = self.netD(target)
            d_real_loss = self.criterionD(d_real, real_label)

            d_fake = self.netD(self.netG(data))
            d_fake_loss = self.criterionD(d_fake, fake_label)
            d_total = d_real_loss + d_fake_loss
            d_train_loss += d_total.item()
            d_total.backward()
            self.optimizerD.step()

            # Train generator
            self.optimizerG.zero_grad()
            g_real = self.netG(data)
            g_fake = self.netD(g_real)
            gan_loss = self.criterionD(g_fake, real_label)
            mse_loss = self.criterionG(g_real, target)

            g_total = mse_loss + 1e-3 * gan_loss
            g_train_loss += g_total.item()
            g_total.backward()
            self.optimizerG.step()

            progress_bar(batch_num, len(self.training_loader), 'G_Loss: %.4f | D_Loss: %.4f' % (g_train_loss / (batch_num + 1), d_train_loss / (batch_num + 1)))

        print("    Average G_Loss: {:.4f}".format(g_train_loss / len(self.training_loader)))

    def test(self):
        self.netG.eval()
        avg_psnr = 0

        with torch.no_grad():
            for batch_num, (data, target) in enumerate(self.testing_loader):
                data, target = data.to(self.device), target.to(self.device)
                prediction = self.netG(data)
                mse = self.criterionG(prediction, target)
                psnr = 10 * log10(1 / mse.item())
                avg_psnr += psnr
                progress_bar(batch_num, len(self.testing_loader), 'PSNR: %.4f' % (avg_psnr / (batch_num + 1)))

        print("    Average PSNR: {:.4f} dB".format(avg_psnr / len(self.testing_loader)))
        return avg_psnr / len(self.testing_loader)

    def run(self):
        self.build_model()
        all_epoch_psnrs = []
        for epoch in range(1, self.epoch_pretrain + 1):
            self.pretrain()
            print("{}/{} pretrained".format(epoch, self.epoch_pretrain))

        for epoch in range(1, self.nEpochs + 1):
            print("\n===> Epoch {} starts:".format(epoch))
            self.train()
            epoch_psnr = self.test()
            all_epoch_psnrs.append(epoch_psnr)
            self.scheduler.step()
            # if epoch == self.nEpochs:
            self.save_model(epoch)
        
        best_epoch = argmax(all_epoch_psnrs) + 1
        print("Best epoch: model_{} with PSNR {}".format(best_epoch, all_epoch_psnrs[best_epoch - 1]))
        copyfile(self.model_path + "/model_{}.pth".format(best_epoch), self.model_path + "/best_model.pth")

        with open(self.model_path + '/metrics.txt', 'w+') as metricsfile:
            print("Saving metrics")
            for i, psnr in enumerate(all_epoch_psnrs):
                metricsfile.write("{},{}\n".format(i+1, psnr))
            metricsfile.write("Best epoch: model_{} with PSNR {}\n".format(best_epoch, all_epoch_psnrs[best_epoch - 1]))
