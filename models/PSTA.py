import torch
from  torch import nn
import  torch.utils.model_zoo as model_zoo
from torch.nn import functional as F

from cam_functions import visual_batch
from models.backbone.resnet import *
from models.STAM import STAM

model_urls = {
    'resnet18': 'https://download.pytorch.org/models/resnet18-5c106cde.pth',
    'resnet34': 'https://download.pytorch.org/models/resnet34-333f7ec4.pth',
    'resnet50': 'https://download.pytorch.org/models/resnet50-19c8e357.pth',
    'resnet101': 'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth',
    'resnet152': 'https://download.pytorch.org/models/resnet152-b121ed2d.pth',
    'resnext50_32x4d': 'https://download.pytorch.org/models/resnext50_32x4d-7cdf4587.pth',
    'resnext101_32x8d': 'https://download.pytorch.org/models/resnext101_32x8d-8ba56ff5.pth',
}

def init_pretrained_weight(model, model_url):
    """Initializes model with pretrained weight

    Layers that don't match with pretrained layers in name or size are kept unchanged
    """
    pretrain_dict = model_zoo.load_url(model_url)
    model_dict = model.state_dict()
    pretrain_dict = {k: v for k, v in pretrain_dict.items() if k in model_dict and model_dict[k].size() == v.size()}
    model_dict.update(pretrain_dict)
    model.load_state_dict(model_dict)

def weights_init_kaiming(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_out')
        nn.init.constant_(m.bias, 0.0)
    elif classname.find('Conv') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in')
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)
    elif classname.find('BatchNorm') != -1:
        if m.affine:
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0.0)

def weight_init_classifier(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.normal_(m.weight, std=0.001)
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)

class PSTA(nn.Module):

    def __init__(self, num_classes, model_name, pretrain_choice, seq_len=8):
        super(PSTA, self).__init__()

        self.in_planes = 2048
        self.base = ResNet()

        if pretrain_choice == 'imagenet':
            init_pretrained_weight(self.base, model_urls[model_name])
            print('Loading pretrained ImageNet model ......')

        self.seq_len = seq_len
        self.num_classes = num_classes
        self.plances = 1024
        self.mid_channel = 256
        self.avg_2d = nn.AdaptiveAvgPool2d((1, 1))
        self.avg_3d = nn.AdaptiveAvgPool3d((1, 1, 1))
        self.relu = nn.ReLU(inplace=True)
        self.sigmoid = nn.Sigmoid()
        #add
        self.channel_attention = nn.Sequential(
            nn.Linear(in_features=1024, out_features=int(128)),
            self.relu,
            nn.Linear(in_features=int(128), out_features=1024),
            self.sigmoid
        )
        self.channel_attention.apply(weights_init_kaiming)

        self.down_channel = nn.Sequential(
            nn.Conv2d(in_channels=self.in_planes, out_channels=self.plances, kernel_size=1, stride=1, padding=0, bias=False),
            nn.BatchNorm2d(self.plances),
            self.relu
        )

        # self.theta_channel = nn.Sequential(#θ
        #     nn.Conv1d(in_channels=1024, out_channels=int(1024 / 8),
        #               kernel_size=1, stride=1, padding=0, bias=False),
        #     self.relu,
        # )
        # self.theta_channel.apply(weights_init_kaiming)
        t = seq_len
        self.layer1 = STAM(inplanes=self.plances, mid_planes=self.mid_channel, seq_len=t / 2, num= '1')

        t = t / 2
        self.layer2 = STAM(inplanes=self.plances, mid_planes=self.mid_channel, seq_len=t / 2, num= '2')

        t = t / 2
        self.layer3 = STAM(inplanes=self.plances, mid_planes=self.mid_channel, seq_len=t / 2, num= '3')


        self.bottleneck = nn.ModuleList([nn.BatchNorm1d(self.plances) for _  in range(3)])
        self.classifier = nn.ModuleList([nn.Linear(self.plances, num_classes) for _ in range(3)])

        self.bottleneck[0].bias.requires_grad_(False)
        self.bottleneck[1].bias.requires_grad_(False)
        self.bottleneck[2].bias.requires_grad_(False)

        self.bottleneck.apply(weights_init_kaiming)
        self.classifier.apply(weight_init_classifier)

    def forward(self, x, pids=None, camid=None):
        b, t, c, w, h = x.size()
        #print(x.size())
        #
        x = x.view(b * t, c, w, h)
        feat_map = self.base(x)  # (b * t, c, 16, 8)
        w = feat_map.size(2)
        h = feat_map.size(3)
       # print(feat_map.shape)
        feat_map = self.down_channel(feat_map)
      #  print(feat_map.shape)
        feat_map = feat_map.view(b, t, -1, w, h)
       # print(feat_map.shape)
        feature_list = []
        list = []

        feat_map_1,spa_att1 = self.layer1(feat_map)  # b x 4 x 1024 x 16 x 8
        y = x.reshape(b, t, 3, 256, 128)
        for i in range(spa_att1.size(0)):
            for j in range(spa_att1.size(1)):
                #img2 = y[i][j].squeeze().cpu().numpy().transpose(1, 2, 0)
                visual_batch(spa_att1[i][j], y[i][j], i, j, "ours")
        # reshape_map = feat_map_1.view(-1, 1024, 16, 8) # add
        # feat_vect = self.avg_2d(reshape_map).view(16, 4, -1) #gap  b x 4 x 1024  add
        # channel_para = self.theta_channel(feat_vect.permute(0, 2, 1))#add
        # para0 = torch.cat((channel_para[:, :, 0], channel_para[:, :, 1], channel_para[:, :, 2], channel_para[:, :, 3]), 1)#add
        # para_00 = self.channel_attention(para0).view(16, -1, 1, 1)  # 大小（16 x 1024 x 1 x 1)
        # feature_1 = torch.mean(feat_map_1, 1)*para_00 # b x 1024 x 16 x 8
        ''' 
        b, t, c, w, h = feat_map_1.size()
        feat_vect = self.avg_2d(feat_map_1).view(b, t, -1)  # gap  b x 4 x 1024  add
        para_00 =self.channel_attention(feat_vect).view(b, t, 1024, 1,1)
        feat_map_1 = feat_map_1 * para_00
        # add cbam 
        # input: b x t x c x w x h
        # output: b x t x c x w x h
        # add multi-
        feature_1 = torch.sum(feat_map_1, 1)
        '''
        feature_1 = torch.mean(feat_map_1, 1)
        #b, t, c, w, h = feat_map_1.size()
        #feat_vect = self.avg_2d(feat_map_1).view(b, t, -1)      # b x t x 1024
        #para00 = self.channel_attention(feat_vect) # b x t x 1024
        #feature1 = torch.sum((feat_vect * para00),1)
        feature1 = self.avg_2d(feature_1).view(b, -1)
        feature_list.append(feature1)
        list.append(feature1)

        feat_map_2,spa_att2 = self.layer2(feat_map_1)
        feature_2 = torch.mean(feat_map_2, 1)
        '''
        b, t, c, w, h = feat_map_2.size()
        feat_vect_2 = self.avg_2d(feat_map_2).view(b, t, -1)  # gap  b x 4 x 1024  add
        para_01 =self.channel_attention(feat_vect_2).view(b, t, 1024, 1,1)
        feat_map_2 = feat_map_2 * para_01
        feature_2 = torch.sum(feat_map_2, 1)
        '''
        #b, t, c, w, h = feat_map_2.size()
        #feat_vect_2 = self.avg_2d(feat_map_2).view(b, t, -1)      # b x t x 1024
        #para01 = self.channel_attention(feat_vect_2) # b x t x 1024
        #feature_2 = torch.sum((feat_vect_2 * para01),1) #b x 1024
        feature_2 = self.avg_2d(feature_2).view(b, -1)
        para0 = self.channel_attention(feature_2)
        para1 = self.channel_attention(feature1)
        #list.append(feature_2)

        #feature2 = torch.stack(list, 1)
        #feature2 = torch.mean(feature2, 1)
        feature2 = para0 * feature_2 + para1 * feature1
        feature_list.append(feature2)

        feat_map_3,spa_att3 = self.layer3(feat_map_2)
        feature_3 = torch.mean(feat_map_3, 1)
        feature_3 = self.avg_2d(feature_3).view(b, -1)
        #b, t, c, w, h = feat_map_3.size()
        #feat_vect_3 = self.avg_2d(feat_map_3).view(b, t, -1)      # b x t x 1024
        #para02 = self.channel_attention(feat_vect_3) # b x t x 1024
        #feature_3 = torch.sum((feat_vect_3 * para02),1) #b x 1024
        para3 = self.channel_attention(feature_3)
        para4 = self.channel_attention(feature_2)
        para5 = self.channel_attention(feature1)

        #list.append(feature_3)

        #feature3 = torch.stack(list, 1)
        #feature3 = torch.mean(feature3, 1)
        feature3 = para3 * feature_3 + para4 * feature_2 + para5 * feature1
        feature_list.append(feature3)

        BN_feature_list = []
        for i in range(len(feature_list)):
            BN_feature_list.append(self.bottleneck[i](feature_list[i]))
        torch.cuda.empty_cache()

        cls_score = []
        for i in range(len(BN_feature_list)):
            cls_score.append(self.classifier[i](BN_feature_list[i]))

        if self.training:
            return cls_score, BN_feature_list
        else:
            return BN_feature_list[2], pids, camid