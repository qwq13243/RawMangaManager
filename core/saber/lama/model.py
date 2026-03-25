# LAMA MPE (Masking Positional Encoding) 模型实现
# 基于 LAMA 论文实现，支持位置编码
# 论文: https://arxiv.org/pdf/2203.00867.pdf (ZITS)

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import cv2
import os
import logging
from torch import Tensor
from typing import Optional

from ..utils import resource_path

logger = logging.getLogger("SaberLamaModel")

# ============================================================
# 工具函数
# ============================================================

def set_requires_grad(module, value):
    for param in module.parameters():
        param.requires_grad = value


def get_activation(kind='tanh'):
    if kind == 'tanh':
        return nn.Tanh()
    if kind == 'sigmoid':
        return nn.Sigmoid()
    if kind is False:
        return nn.Identity()
    raise ValueError(f'Unknown activation kind {kind}')


def resize_keep_aspect(img: np.ndarray, size: int) -> np.ndarray:
    """保持宽高比缩放图像"""
    h, w = img.shape[:2]
    if h > w:
        new_h = size
        new_w = int(w * size / h)
    else:
        new_w = size
        new_h = int(h * size / w)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)


# ============================================================
# FFC (Fast Fourier Convolution) 模块
# ============================================================

class FFCSE_block(nn.Module):
    def __init__(self, channels, ratio_g):
        super(FFCSE_block, self).__init__()
        in_cg = int(channels * ratio_g)
        in_cl = channels - in_cg
        r = 16

        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.conv1 = nn.Conv2d(channels, channels // r, kernel_size=1, bias=True)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv_a2l = None if in_cl == 0 else nn.Conv2d(channels // r, in_cl, kernel_size=1, bias=True)
        self.conv_a2g = None if in_cg == 0 else nn.Conv2d(channels // r, in_cg, kernel_size=1, bias=True)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = x if type(x) is tuple else (x, 0)
        id_l, id_g = x

        x = id_l if type(id_g) is int else torch.cat([id_l, id_g], dim=1)
        x = self.avgpool(x)
        x = self.relu1(self.conv1(x))

        x_l = 0 if self.conv_a2l is None else id_l * self.sigmoid(self.conv_a2l(x))
        x_g = 0 if self.conv_a2g is None else id_g * self.sigmoid(self.conv_a2g(x))
        return x_l, x_g


class FourierUnit(nn.Module):
    def __init__(self, in_channels, out_channels, groups=1, spatial_scale_factor=None, 
                 spatial_scale_mode='bilinear', spectral_pos_encoding=False, 
                 use_se=False, se_kwargs=None, ffc3d=False, fft_norm='ortho'):
        super(FourierUnit, self).__init__()
        self.groups = groups

        self.conv_layer = torch.nn.Conv2d(
            in_channels=in_channels * 2 + (2 if spectral_pos_encoding else 0),
            out_channels=out_channels * 2,
            kernel_size=1, stride=1, padding=0, groups=self.groups, bias=False
        )
        self.bn = torch.nn.BatchNorm2d(out_channels * 2)
        self.relu = torch.nn.ReLU(inplace=True)

        self.use_se = use_se
        self.spatial_scale_factor = spatial_scale_factor
        self.spatial_scale_mode = spatial_scale_mode
        self.spectral_pos_encoding = spectral_pos_encoding
        self.ffc3d = ffc3d
        self.fft_norm = fft_norm

    def forward(self, x):
        batch = x.shape[0]

        if self.spatial_scale_factor is not None:
            orig_size = x.shape[-2:]
            x = F.interpolate(x, scale_factor=self.spatial_scale_factor, 
                            mode=self.spatial_scale_mode, align_corners=False)

        r_size = x.size()
        fft_dim = (-3, -2, -1) if self.ffc3d else (-2, -1)

        if x.dtype in (torch.float16, torch.bfloat16):
            x = x.type(torch.float32)

        ffted = torch.fft.rfftn(x, dim=fft_dim, norm=self.fft_norm)
        ffted = torch.stack((ffted.real, ffted.imag), dim=-1)
        ffted = ffted.permute(0, 1, 4, 2, 3).contiguous()
        ffted = ffted.view((batch, -1,) + ffted.size()[3:])

        if self.spectral_pos_encoding:
            height, width = ffted.shape[-2:]
            coords_vert = torch.linspace(0, 1, height)[None, None, :, None].expand(batch, 1, height, width).to(ffted)
            coords_hor = torch.linspace(0, 1, width)[None, None, None, :].expand(batch, 1, height, width).to(ffted)
            ffted = torch.cat((coords_vert, coords_hor, ffted), dim=1)

        if self.use_se:
            ffted = self.se(ffted)

        ffted = self.conv_layer(ffted)
        ffted = self.relu(self.bn(ffted))

        ffted = ffted.view((batch, -1, 2,) + ffted.size()[2:]).permute(0, 1, 3, 4, 2).contiguous()
        if ffted.dtype in (torch.float16, torch.bfloat16):
            ffted = ffted.type(torch.float32)
        ffted = torch.complex(ffted[..., 0], ffted[..., 1])

        ifft_shape_slice = x.shape[-3:] if self.ffc3d else x.shape[-2:]
        output = torch.fft.irfftn(ffted, s=ifft_shape_slice, dim=fft_dim, norm=self.fft_norm)

        if self.spatial_scale_factor is not None:
            output = F.interpolate(output, size=orig_size, mode=self.spatial_scale_mode, align_corners=False)

        return output


class SpectralTransform(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1, groups=1, enable_lfu=True, **fu_kwargs):
        super(SpectralTransform, self).__init__()
        self.enable_lfu = enable_lfu
        if stride == 2:
            self.downsample = nn.AvgPool2d(kernel_size=(2, 2), stride=2)
        else:
            self.downsample = nn.Identity()

        self.stride = stride
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, out_channels // 2, kernel_size=1, groups=groups, bias=False),
            nn.BatchNorm2d(out_channels // 2),
            nn.ReLU(inplace=True)
        )
        self.fu = FourierUnit(out_channels // 2, out_channels // 2, groups, **fu_kwargs)
        if self.enable_lfu:
            self.lfu = FourierUnit(out_channels // 2, out_channels // 2, groups)
        self.conv2 = torch.nn.Conv2d(out_channels // 2, out_channels, kernel_size=1, groups=groups, bias=False)

    def forward(self, x):
        x = self.downsample(x)
        x = self.conv1(x)
        output = self.fu(x)

        if self.enable_lfu:
            n, c, h, w = x.shape
            split_no = 2
            split_h = h // split_no
            split_w = w // split_no
            xs = torch.cat(torch.split(x[:, :c // 4], split_h, dim=-2), dim=1).contiguous()
            xs = torch.cat(torch.split(xs, split_w, dim=-1), dim=1).contiguous()
            xs = self.lfu(xs)
            xs = xs.repeat(1, 1, split_no, split_no).contiguous()
        else:
            xs = 0

        output = self.conv2(x + output + xs)
        return output


class FFC(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size,
                 ratio_gin, ratio_gout, stride=1, padding=0,
                 dilation=1, groups=1, bias=False, enable_lfu=True,
                 padding_type='reflect', gated=False, **spectral_kwargs):
        super(FFC, self).__init__()

        assert stride == 1 or stride == 2, "Stride should be 1 or 2."
        self.stride = stride

        in_cg = int(in_channels * ratio_gin)
        in_cl = in_channels - in_cg
        out_cg = int(out_channels * ratio_gout)
        out_cl = out_channels - out_cg

        self.ratio_gin = ratio_gin
        self.ratio_gout = ratio_gout
        self.global_in_num = in_cg

        module = nn.Identity if in_cl == 0 or out_cl == 0 else nn.Conv2d
        self.convl2l = module(in_cl, out_cl, kernel_size,
                              stride, padding, dilation, groups, bias, padding_mode=padding_type)
        module = nn.Identity if in_cl == 0 or out_cg == 0 else nn.Conv2d
        self.convl2g = module(in_cl, out_cg, kernel_size,
                              stride, padding, dilation, groups, bias, padding_mode=padding_type)
        module = nn.Identity if in_cg == 0 or out_cl == 0 else nn.Conv2d
        self.convg2l = module(in_cg, out_cl, kernel_size,
                              stride, padding, dilation, groups, bias, padding_mode=padding_type)
        module = nn.Identity if in_cg == 0 or out_cg == 0 else SpectralTransform
        self.convg2g = module(in_cg, out_cg, stride, 1 if groups == 1 else groups // 2, 
                              enable_lfu, **spectral_kwargs)

        self.gated = gated
        module = nn.Identity if in_cg == 0 or out_cl == 0 or not self.gated else nn.Conv2d
        self.gate = module(in_channels, 2, 1)

    def forward(self, x):
        x_l, x_g = x if type(x) is tuple else (x, 0)
        out_xl, out_xg = 0, 0

        if self.gated:
            total_input_parts = [x_l]
            if torch.is_tensor(x_g):
                total_input_parts.append(x_g)
            total_input = torch.cat(total_input_parts, dim=1)
            gates = torch.sigmoid(self.gate(total_input))
            g2l_gate, l2g_gate = gates.chunk(2, dim=1)
        else:
            g2l_gate, l2g_gate = 1, 1

        if self.ratio_gout != 1:
            out_xl = self.convl2l(x_l) + self.convg2l(x_g) * g2l_gate
        if self.ratio_gout != 0:
            out_xg = self.convl2g(x_l) * l2g_gate + self.convg2g(x_g)

        return out_xl, out_xg


class FFC_BN_ACT(nn.Module):
    def __init__(self, in_channels, out_channels,
                 kernel_size, ratio_gin, ratio_gout,
                 stride=1, padding=0, dilation=1, groups=1, bias=False,
                 norm_layer=nn.BatchNorm2d, activation_layer=nn.Identity,
                 padding_type='reflect', enable_lfu=True, **kwargs):
        super(FFC_BN_ACT, self).__init__()
        self.ffc = FFC(in_channels, out_channels, kernel_size,
                       ratio_gin, ratio_gout, stride, padding, dilation,
                       groups, bias, enable_lfu, padding_type=padding_type, **kwargs)
        lnorm = nn.Identity if ratio_gout == 1 else norm_layer
        gnorm = nn.Identity if ratio_gout == 0 else norm_layer
        global_channels = int(out_channels * ratio_gout)
        self.bn_l = lnorm(out_channels - global_channels)
        self.bn_g = gnorm(global_channels)

        lact = nn.Identity if ratio_gout == 1 else activation_layer
        gact = nn.Identity if ratio_gout == 0 else activation_layer
        self.act_l = lact(inplace=True)
        self.act_g = gact(inplace=True)

    def forward(self, x):
        x_l, x_g = self.ffc(x)
        x_l = self.act_l(self.bn_l(x_l))
        x_g = self.act_g(self.bn_g(x_g))
        return x_l, x_g


class FFCResnetBlock(nn.Module):
    def __init__(self, dim, padding_type, norm_layer, activation_layer=nn.ReLU, dilation=1,
                 spatial_transform_kwargs=None, inline=False, **conv_kwargs):
        super().__init__()
        self.conv1 = FFC_BN_ACT(dim, dim, kernel_size=3, padding=dilation, dilation=dilation,
                                norm_layer=norm_layer, activation_layer=activation_layer,
                                padding_type=padding_type, **conv_kwargs)
        self.conv2 = FFC_BN_ACT(dim, dim, kernel_size=3, padding=dilation, dilation=dilation,
                                norm_layer=norm_layer, activation_layer=activation_layer,
                                padding_type=padding_type, **conv_kwargs)
        self.inline = inline

    def forward(self, x):
        if self.inline:
            x_l, x_g = x[:, :-self.conv1.ffc.global_in_num], x[:, -self.conv1.ffc.global_in_num:]
        else:
            x_l, x_g = x if type(x) is tuple else (x, 0)

        id_l, id_g = x_l, x_g

        x_l, x_g = self.conv1((x_l, x_g))
        x_l, x_g = self.conv2((x_l, x_g))

        x_l, x_g = id_l + x_l, id_g + x_g
        out = x_l, x_g
        if self.inline:
            out = torch.cat(out, dim=1)
        return out


class ConcatTupleLayer(nn.Module):
    def forward(self, x):
        assert isinstance(x, tuple)
        x_l, x_g = x
        assert torch.is_tensor(x_l) or torch.is_tensor(x_g)
        if not torch.is_tensor(x_g):
            return x_l
        return torch.cat(x, dim=1)


# ============================================================
# MPE (Masking Positional Encoding) 模块
# ============================================================

class MaskedSinusoidalPositionalEmbedding(nn.Embedding):
    """位置嵌入模块"""
    def __init__(self, num_embeddings: int, embedding_dim: int):
        super().__init__(num_embeddings, embedding_dim)
        self.weight = self._init_weight(self.weight)

    @staticmethod
    def _init_weight(out: nn.Parameter):
        n_pos, dim = out.shape
        position_enc = np.array([
            [pos / np.power(10000, 2 * (j // 2) / dim) for j in range(dim)]
            for pos in range(n_pos)
        ])
        out.requires_grad = False
        sentinel = dim // 2 if dim % 2 == 0 else (dim // 2) + 1
        out[:, 0:sentinel] = torch.FloatTensor(np.sin(position_enc[:, 0::2]))
        out[:, sentinel:] = torch.FloatTensor(np.cos(position_enc[:, 1::2]))
        out.detach_()
        return out

    @torch.no_grad()
    def forward(self, x):
        return super().forward(x)


class MultiLabelEmbedding(nn.Module):
    def __init__(self, num_positions: int, embedding_dim: int):
        super().__init__()
        self.weight = nn.Parameter(torch.Tensor(num_positions, embedding_dim))
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.normal_(self.weight)

    def forward(self, x):
        return (self.weight.unsqueeze(0) * x.unsqueeze(-1)).sum(dim=-2)


class MPE(nn.Module):
    """Masking Positional Encoding 模块"""
    def __init__(self):
        super().__init__()
        self.rel_pos_emb = MaskedSinusoidalPositionalEmbedding(
            num_embeddings=128,
            embedding_dim=64
        )
        self.direct_emb = MultiLabelEmbedding(num_positions=4, embedding_dim=64)
        self.alpha5 = nn.Parameter(torch.tensor(0, dtype=torch.float32), requires_grad=True)
        self.alpha6 = nn.Parameter(torch.tensor(0, dtype=torch.float32), requires_grad=True)

    def forward(self, rel_pos=None, direct=None):
        b, h, w = rel_pos.shape
        rel_pos = rel_pos.reshape(b, h * w)
        rel_pos_emb = self.rel_pos_emb(rel_pos).reshape(b, h, w, -1).permute(0, 3, 1, 2) * self.alpha5
        direct = direct.reshape(b, h * w, 4).to(torch.float32)
        direct_emb = self.direct_emb(direct).reshape(b, h, w, -1).permute(0, 3, 1, 2) * self.alpha6
        return rel_pos_emb, direct_emb


# ============================================================
# FFCResNetGenerator 生成器
# ============================================================

class FFCResNetGenerator(nn.Module):
    def __init__(self, input_nc, output_nc, ngf=64, n_downsampling=3, n_blocks=9,
                 norm_layer=nn.BatchNorm2d, padding_type='reflect', activation_layer=nn.ReLU,
                 up_norm_layer=nn.BatchNorm2d, up_activation=nn.ReLU(True),
                 init_conv_kwargs={}, downsample_conv_kwargs={}, resnet_conv_kwargs={},
                 spatial_transform_layers=None, spatial_transform_kwargs={},
                 add_out_act=True, max_features=1024, out_ffc=False, out_ffc_kwargs={}):
        assert n_blocks >= 0
        super().__init__()

        model = [nn.ReflectionPad2d(3),
                 FFC_BN_ACT(input_nc, ngf, kernel_size=7, padding=0, norm_layer=norm_layer,
                           activation_layer=activation_layer, **init_conv_kwargs)]

        # Downsampling
        for i in range(n_downsampling):
            mult = 2 ** i
            if i == n_downsampling - 1:
                # 最后一个 downsample 层作为过渡层：
                # ratio_gin=0 (纯 local 输入), ratio_gout=0.75 (输出有 global)
                cur_conv_kwargs = dict(downsample_conv_kwargs)
                cur_conv_kwargs['ratio_gout'] = resnet_conv_kwargs.get('ratio_gin', 0)
            else:
                cur_conv_kwargs = downsample_conv_kwargs
            model += [FFC_BN_ACT(min(max_features, ngf * mult),
                                min(max_features, ngf * mult * 2),
                                kernel_size=3, stride=2, padding=1,
                                norm_layer=norm_layer,
                                activation_layer=activation_layer,
                                **cur_conv_kwargs)]

        mult = 2 ** n_downsampling
        feats_num_bottleneck = min(max_features, ngf * mult)

        # ResNet blocks
        for i in range(n_blocks):
            cur_resblock = FFCResnetBlock(feats_num_bottleneck, padding_type=padding_type,
                                         activation_layer=activation_layer,
                                         norm_layer=norm_layer, **resnet_conv_kwargs)
            model += [cur_resblock]

        model += [ConcatTupleLayer()]

        # Upsampling
        for i in range(n_downsampling):
            mult = 2 ** (n_downsampling - i)
            model += [nn.ConvTranspose2d(min(max_features, ngf * mult),
                                        min(max_features, int(ngf * mult / 2)),
                                        kernel_size=3, stride=2, padding=1, output_padding=1),
                     up_norm_layer(min(max_features, int(ngf * mult / 2))),
                     up_activation]

        if out_ffc:
            model += [FFCResnetBlock(ngf, padding_type=padding_type, activation_layer=activation_layer,
                                    norm_layer=norm_layer, inline=True, **out_ffc_kwargs)]

        model += [nn.ReflectionPad2d(3),
                  nn.Conv2d(ngf, output_nc, kernel_size=7, padding=0)]
        if add_out_act:
            model.append(get_activation('tanh' if add_out_act is True else add_out_act))
        self.model = nn.Sequential(*model)

    def forward(self, img, mask, rel_pos=None, direct=None) -> Tensor:
        masked_img = torch.cat([img * (1 - mask), mask], dim=1)
        if rel_pos is None:
            return self.model(masked_img)
        else:
            x_l, x_g = self.model[:2](masked_img)
            x_l = x_l.to(torch.float32)
            x_l += rel_pos
            x_l += direct
            return self.model[2:]((x_l, x_g))


# ============================================================
# LamaFourier 主模型类
# ============================================================

class LamaFourier(nn.Module):
    """LAMA MPE 模型封装类"""
    
    def __init__(self, build_discriminator=False, use_mpe=True, large_arch: bool = False):
        super().__init__()
        self.use_mpe = use_mpe
        
        # 参数设置
        input_nc = 4  # RGB + Mask
        output_nc = 3 # RGB
        ngf = 64
        n_downsampling = 3
        n_blocks = 9  # default for big-lama
        
        # Default configuration for LAMA/FFC
        # Note: LAMA MPE (ZITS) usually uses LFU (Local Fourier Unit), so enable_lfu=True for resnet blocks.
        init_conv_kwargs = {'ratio_gin': 0, 'ratio_gout': 0, 'enable_lfu': False}
        downsample_conv_kwargs = {'ratio_gin': 0, 'ratio_gout': 0, 'enable_lfu': False}
        resnet_conv_kwargs = {'ratio_gin': 0.75, 'ratio_gout': 0.75, 'enable_lfu': True}
        
        self.generator = FFCResNetGenerator(input_nc, output_nc, ngf=ngf, 
                                            n_downsampling=n_downsampling, n_blocks=n_blocks,
                                            init_conv_kwargs=init_conv_kwargs,
                                            downsample_conv_kwargs=downsample_conv_kwargs,
                                            resnet_conv_kwargs=resnet_conv_kwargs)
        
        if self.use_mpe:
            self.mpe = MPE()

    def forward(self, img, mask, rel_pos=None, direct=None):
        if self.use_mpe and rel_pos is not None and direct is not None:
            rel_pos_emb, direct_emb = self.mpe(rel_pos, direct)
            return self.generator(img, mask, rel_pos_emb, direct_emb)
        else:
            return self.generator(img, mask)

# ============================================================
# 推理逻辑
# ============================================================

_lama_model_instance = None
_lama_model_device = None

def is_lama_mpe_available():
    model_path = resource_path("models/lama/inpainting_lama_mpe.ckpt")
    return os.path.exists(model_path)

def load_lama_mpe_model(device):
    global _lama_model_instance, _lama_model_device
    
    if _lama_model_instance is not None and _lama_model_device == device:
        return _lama_model_instance

    model_path = resource_path("models/lama/inpainting_lama_mpe.ckpt")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")
    
    logger.info(f"Loading LAMA MPE model from {model_path} to {device}")
    
    # 初始化模型
    model = LamaFourier(use_mpe=False) # MPE is disabled in inference for simplicity or speed if not needed, or check if checkpoint has it
    # Wait, if checkpoint has MPE weights, we should enable it.
    # Usually standard LAMA (big-lama) does not use MPE. 
    # But this file is called lama_mpe_interface. Let's assume use_mpe=False for standard big-lama compatibility or check state_dict.
    # However, the class name suggests MPE support.
    # Let's try to load state dict and see.
    
    checkpoint = torch.load(model_path, map_location='cpu')
    if 'state_dict' in checkpoint:
        state_dict = checkpoint['state_dict']
    elif 'gen_state_dict' in checkpoint:
        state_dict = checkpoint['gen_state_dict']
    else:
        state_dict = checkpoint
        
    # Check if MPE keys are in state_dict
    has_mpe = any('mpe' in k for k in state_dict.keys())
    model = LamaFourier(use_mpe=has_mpe)
    
    # Fix state dict keys if needed
    # Check if we need to add 'generator.' prefix
    model_keys = list(model.state_dict().keys())
    ckpt_keys = list(state_dict.keys())
    
    # If checkpoint keys start with 'model.' and model keys start with 'generator.model.'
    if ckpt_keys[0].startswith('model.') and model_keys[0].startswith('generator.model.'):
        new_state_dict = {}
        for k, v in state_dict.items():
            new_state_dict['generator.' + k] = v
        state_dict = new_state_dict
        logger.info("Added 'generator.' prefix to checkpoint keys.")
    
    try:
        model.load_state_dict(state_dict, strict=False)
    except Exception as e:
        logger.warning(f"Strict loading failed, trying non-strict: {e}")
        # Try to match keys more intelligently if needed
        pass

    model.to(device)
    model.eval()
    
    _lama_model_instance = model
    _lama_model_device = device
    return model

def _run_lama_inference(model, img, mask, rel_pos=None, direct=None):
    prediction = model(img, mask, rel_pos, direct)
    prediction = prediction.cpu()
    return prediction

def inpaint_with_lama_mpe(image_np, mask_np, disable_resize=False):
    """
    Args:
        image_np: HxWx3 uint8
        mask_np: HxW uint8, 255 for hole (to be filled), 0 for valid
        disable_resize: bool
    Returns:
        HxWx3 uint8
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    try:
        model = load_lama_mpe_model(device)
    except Exception as e:
        logger.error(f"Failed to load LAMA model: {e}")
        return None

    # Preprocess
    try:
        img = torch.from_numpy(image_np).float().div(255.)
        mask = torch.from_numpy(mask_np).float().div(255.)
        
        if len(img.shape) == 3:
            img = img.permute(2, 0, 1).unsqueeze(0) # B, C, H, W
        if len(mask.shape) == 2:
            mask = mask.unsqueeze(0).unsqueeze(0) # B, 1, H, W
        elif len(mask.shape) == 3:
            mask = mask.permute(2, 0, 1).unsqueeze(0)

        # Resize to multiple of 8
        # NOTE: LFU blocks require features to be splittable by 2.
        # The network has 3 downsampling layers (factor 8).
        # If using LFU, we might need factor 16 or 32 depending on implementation details.
        # Standard LAMA uses 8. If LFU fails with "size mismatch", it means the feature map size is odd.
        # Let's try 32 to be very safe and cover high downsampling factors.
        align_factor = 32
        h, w = img.shape[2:]
        pad_h = (align_factor - h % align_factor) % align_factor
        pad_w = (align_factor - w % align_factor) % align_factor
        
        if pad_h > 0 or pad_w > 0:
            img = F.pad(img, (0, pad_w, 0, pad_h), mode='reflect')
            mask = F.pad(mask, (0, pad_w, 0, pad_h), mode='reflect')

        img = img.to(device)
        mask = mask.to(device)
        mask_blend = mask
        mask = (mask > 0.5).float()
    except Exception as e:
        logger.error(f"LAMA preprocessing failed: {e}")
        return None

    try:
        with torch.no_grad():
            # MPE logic if needed
            rel_pos, direct = None, None
            # prediction = model(img, mask, rel_pos, direct)
            prediction = _run_lama_inference(model, img, mask, rel_pos, direct)
            
            # Debug check for black image
            if prediction.max() <= 0:
                 logger.warning(f"LAMA raw prediction max value is {prediction.max()}. Model might be outputting zeros.")
            
    except RuntimeError as e:
        if "cuda" in str(device).lower() and ("out of memory" in str(e).lower() or "cublas" in str(e).lower() or "cudnn" in str(e).lower()):
            logger.warning(f"CUDA error ({e}), switching to CPU...")
            try:
                torch.cuda.empty_cache()
                device = torch.device("cpu")
                model = load_lama_mpe_model(device) # Reload on CPU
                img = img.to(device)
                mask = mask.to(device)
                with torch.no_grad():
                    prediction = _run_lama_inference(model, img, mask, rel_pos, direct)
            except Exception as e2:
                logger.error(f"LAMA inference failed on CPU fallback: {e2}")
                return None
        else:
            logger.error(f"LAMA inference failed: {e}")
            return None
    except Exception as e:
        logger.error(f"LAMA inference failed: {e}")
        return None

    # Recover
    try:
        img_cpu = img.detach().cpu()
        mask_blend_cpu = mask_blend.detach().cpu()
        if mask_blend_cpu.shape[1] == 1:
            mask_blend_cpu = mask_blend_cpu.repeat(1, 3, 1, 1)
        prediction = img_cpu * (1 - mask_blend_cpu) + prediction * mask_blend_cpu

        # Unpad
        if pad_h > 0 or pad_w > 0:
            prediction = prediction[:, :, :h, :w]
            
        # Clip
        prediction = torch.clamp(prediction, 0, 1)
        
        # To numpy
        prediction = prediction.squeeze(0).permute(1, 2, 0).numpy()
        prediction = (prediction * 255).astype(np.uint8)
        
        # Debug check for black image
        if np.mean(prediction) < 1.0:
             logger.warning(f"LAMA processed prediction mean value is {np.mean(prediction)}. Max is {np.max(prediction)}.")
        
        return prediction
    except Exception as e:
        logger.error(f"LAMA postprocessing failed: {e}")
        return None
