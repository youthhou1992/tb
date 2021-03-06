import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
#from layers.functions import prior_box
#from layers.functions import detection
from layers import *
from layers.modules import l2norm
from data import text
import os


class TB(nn.Module):
    """Single Shot Multibox Architecture
    The network is composed of a base VGG network followed by the
    added multibox conv layers.  Each multibox layer branches into
        1) conv2d for class conf scores
        2) conv2d for localization predictions
        3) associated priorbox layer to produce default bounding
           boxes specific to the layer's feature map size.
    See: https://arxiv.org/pdf/1512.02325.pdf for more details.

    Args:
        phase: (string) Can be "test" or "train"
        size: input image size
        base: VGG16 layers for input, size of either 300 or 500
        extras: extra layers that feed to multibox loc and conf layers
        head: "multibox head" consists of loc and conf conv layers
    """

    def __init__(self, phase, size, base, extras, head, num_classes):
        super(TB, self).__init__()
        self.phase = phase
        self.num_classes = num_classes
        #self.cfg = (coco, voc)[num_classes == 21]
        self.cfg = text
        self.priorbox = PriorBox(self.cfg)
        with torch.no_grad():
            self.priors = Variable(self.priorbox.forward())
        self.size = size

        # SSD network
        self.vgg = nn.ModuleList(base)
        # Layer learns to scale the l2 normalized features from conv4_3
        self.L2Norm = l2norm.L2Norm(512, 20)
        self.extras = nn.ModuleList(extras) #通过列表建立网络

        self.loc = nn.ModuleList(head[0])
        self.conf = nn.ModuleList(head[1])

        if phase == 'test':
            self.softmax = nn.Softmax(dim=-1)
            self.detect = Detect(num_classes, 0, 200, 0.01, 0.45) #在测试阶段，调用NMS函数

    def forward(self, x):
        """Applies network layers and ops on input image(s) x.

        Args:
            x: input image or batch of images. Shape: [batch,3,300,300].

        Return:
            Depending on phase:
            test:
                Variable(tensor) of output class label predictions,
                confidence score, and corresponding location predictions for
                each object detected. Shape: [batch,topk,7]

            train:
                list of concat outputs from:
                    1: confidence layers, Shape: [batch*num_priors,num_classes]
                    2: localization layers, Shape: [batch,num_priors*4]
                    3: priorbox layers, Shape: [2,num_priors*4]
        """
        sources = list()
        loc = list()
        conf = list()
        #print('x.size', x.data.size)
        # apply vgg up to conv4_3 relu
        for k in range(23):
            #print (k, x)
            x = self.vgg[k](x)

        s = self.L2Norm(x)
        sources.append(s)

        # apply vgg up to fc7
        for k in range(23, len(self.vgg)):
            x = self.vgg[k](x)
        sources.append(x)

        # apply extra layers and cache source layer outputs
        #倒数第二层不添加到sources中
        for k, v in enumerate(self.extras[:-2]):
            #print('k:', k)
            x = F.relu(v(x), inplace=True)
            if k % 2 == 1 :
                sources.append(x)
        #最后两层
        x = F.relu(self.extras[-2](x), inplace=True)
        x = F.relu(self.extras[-1](x), inplace=True)
        sources.append(x)

        # apply multibox head to source layers
        for (x, l, c) in zip(sources, self.loc, self.conf):
            loc.append(l(x).permute(0, 2, 3, 1).contiguous())
            conf.append(c(x).permute(0, 2, 3, 1).contiguous())

        loc = torch.cat([o.view(o.size(0), -1) for o in loc], 1)
        #print('loc', loc)
        conf = torch.cat([o.view(o.size(0), -1) for o in conf], 1)
        if self.phase == "test":
            output = self.detect(
                loc.view(loc.size(0), -1, 4),                   # loc preds
                self.softmax(conf.view(conf.size(0), -1,
                             self.num_classes)),                # conf preds
                self.priors.type(type(x.data))                  # default boxes
            )
        else:
            output = (
                loc.view(loc.size(0), -1, 4),
                conf.view(conf.size(0), -1, self.num_classes),
                self.priors
            )
        # output = (
        #     loc.view(loc.size(0), -1, 4),
        #     conf.view(conf.size(0), -1, self.num_classes),
        #     self.priors
        # )
        return output

    def load_weights(self, base_file):
        other, ext = os.path.splitext(base_file)
        if ext == '.pkl' or '.pth':
            print('Loading weights into state dict...')
            self.load_state_dict(torch.load(base_file,
                                 map_location=lambda storage, loc: storage))
            print('Finished!')
        else:
            print('Sorry only .pth and .pkl files supported.')


# This function is derived from torchvision VGG make_layers()
# https://github.com/pytorch/vision/blob/master/torchvision/models/vgg.py
#vgg 部分与ssd相同
def vgg(i, batch_norm=False):
    layers = []
    in_channels = i
    for v in VGG:
        if v == 'M':
            layers += [nn.MaxPool2d(kernel_size=2, stride=2)]
        elif v == 'C':
            layers += [nn.MaxPool2d(kernel_size=2, stride=2, ceil_mode=True)]
        else:
            conv2d = nn.Conv2d(in_channels, v, kernel_size=3, padding=1)
            if batch_norm:
                layers += [conv2d, nn.BatchNorm2d(v), nn.ReLU(inplace=True)]
            else:
                layers += [conv2d, nn.ReLU(inplace=True)]
            in_channels = v
    pool5 = nn.MaxPool2d(kernel_size=3, stride=1, padding=1)
    conv6 = nn.Conv2d(512, 1024, kernel_size=3, padding=6, dilation=6)
    conv7 = nn.Conv2d(1024, 1024, kernel_size=1)
    layers += [pool5, conv6,
               nn.ReLU(inplace=True), conv7, nn.ReLU(inplace=True)]
    return layers

#与ssd相比：
#１conv8_1, conv8_2, pool6不同


def add_extras(i):
    # Extra layers added to VGG for feature scaling
    layers = []
    in_channels = i
    flag = False
    for k, v in enumerate(EXTRAS):
        if in_channels != 'S':
            if v == 'S':
                layers += [nn.Conv2d(in_channels, EXTRAS[k + 1],
                           kernel_size=(1, 3)[flag], stride=2, padding=1)]
            else:
                layers += [nn.Conv2d(in_channels, v, kernel_size=(1, 3)[flag])]
            flag = not flag
        in_channels = v
    conv8_1 = nn.Conv2d(256, 128, kernel_size=1)
    conv8_2 = nn.Conv2d(128, 256, kernel_size=3, stride=2, padding=1)
    #全局平均池化
    pool6 = nn.AdaptiveAvgPool2d(1)
    layers += [conv8_1, conv8_2, pool6]
    return layers


def multibox(vgg, extra_layers, num_classes):
    loc_layers = []
    conf_layers = []
    #vgg_source = [21, -2]#conv4_3(作者的caffe代码中，conv4_3后应该接L2norm层), conv7
    #for k, v in enumerate(vgg_source):
    #conv4_3
    loc_layers += [nn.Conv2d(vgg[21].out_channels,
                                 56, kernel_size=(1,5),padding=(0,2))]
    conf_layers += [nn.Conv2d(vgg[21].out_channels,
                        28, kernel_size=(1,5), padding=(0,2))]

    #conv7
    loc_layers += [nn.Conv2d(vgg[-2].out_channels,
                             56, kernel_size=(1, 5), padding=(0, 2))]
    conf_layers += [nn.Conv2d(vgg[-2].out_channels,
                              28, kernel_size=(1, 5), padding=(0, 2))]
    #此处实现与论文不同，但是与作者的caffe代码是相同的
    #不同处在于pool6提取特征时采用的卷积核大小，论文中为1*1的卷积
    #print(extra_layers)
    for k, v in enumerate(extra_layers[1::2]):
        loc_layers += [nn.Conv2d(v.out_channels, 56, kernel_size=(1,5), padding=(0,2))]
        conf_layers += [nn.Conv2d(v.out_channels, 28
                                  , kernel_size=(1,5), padding=(0,2))]
    # print('loc_layers', loc_layers)
    # print('conf_layers', conf_layers)
    return vgg, extra_layers, (loc_layers, conf_layers)



VGG= [64, 64, 'M', 128, 128, 'M', 256, 256, 256, 'C', 512, 512, 512, 'M',
            512, 512, 512]

EXTRAS = [256, 'S', 512, 128, 'S', 256, 128, 256]

#MBOX = [4, 6, 6, 6, 4, 4]  # number of boxes per feature map location



def build_tb(phase, size=300, num_classes=2):
    if phase != "test" and phase != "train":
        print("ERROR: Phase: " + phase + " not recognized")
        return
    # if size != 300:
    #     print("ERROR: You specified size " + repr(size) + ". However, " +
    #           "currently only SSD300 (size=300) is supported!")
    #     return
    base_, extras_, head_ = multibox(vgg(3),
                                     add_extras(1024),
                                     num_classes)
    return TB(phase, size, base_, extras_, head_, num_classes)


if __name__ == '__main__':
    net = build_tb('train')
    print(net)