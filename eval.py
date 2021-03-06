import argparse
import torch
import os, cv2
import tb
import torch.backends.cudnn as cudnn
import numpy as np
from torch.autograd import Variable

'''
    use DetEval calculated recall, precision, F1 score
    https://perso.liris.cnrs.fr/christian.wolf/software/deteval/index.html
'''

parser = argparse.ArgumentParser(description= 'TextBoxes detection')
parser.add_argument('--trained_model', default= 'weights/tb_80000.pth',
                    type = str, help='trained model to use')
parser.add_argument('--visual_threshold', default=0.5, type=float,
                    help='Final confidence threshold')
parser.add_argument('--cuda', default=True, type=bool,
                    help='Use cuda to train model')
args = parser.parse_args()

if args.cuda and torch.cuda.is_available():
    torch.set_default_tensor_type('torch.cuda.FloatTensor')
else:
    torch.set_default_tensor_type('torch.FloatTensor')

mean = [104, 117, 123]
#if not os.path.exists(args.sa)
def eval_net(net, dataset, cuda, thresh):
    det_bbox = []
    det_img = []
    for image in os.listdir(dataset):
        det_img.append(image)
        img_path = os.path.join(dataset, image)
        img = cv2.imread(img_path)
        img = np.array(img, np.float32)
        img = img - mean
        img_resized = cv2.resize(img, (300, 300))
        img = img_resized[:, :, (2, 1, 0)]
        x = torch.from_numpy(img).permute(2, 0, 1)
        x = x.type(torch.FloatTensor)
        x = Variable(x.unsqueeze(0))
        if cuda:
            x = x.cuda()
        y = net(x)
        detections = y.data
        scale = torch.Tensor([img.shape[1], img.shape[0],
                             img.shape[1], img.shape[0]])
        i = 0
        single_box = []
        while(detections[0, 1, i, 0] >= thresh):
            pt = (detections[0, 1, i, 1:]*scale).cpu().numpy()
            box = [int(p) for p in pt]
            single_box.append(box)
            i += 1
        det_bbox.append(single_box)
        #break
    return det_img, det_bbox

def write2txt(res_root, img, bboxs):
    for i, im in enumerate(img):
        img_id = im.split('.')[0]
        txt_path = os.path.join(res_root, 'res_' + img_id + '.txt')
        print(txt_path)
        bbox = bboxs[i]
        print(bbox)
        with open(txt_path, 'w') as f:
            for box in bbox:
                line = ','.join([str(int(box[0])), str(int(box[1])), str(int(box[2])), str(int(box[3]))]) + '\r\n'
                f.write(line)
        #break


def eval_model():
    #加载网络
    net = tb.build_tb('test')
    #加载模型
    net.load_state_dict(torch.load(args.trained_model))
    #eval 模式
    net.eval()
    #cuda, cudnn
    if args.cuda:
        net = net.cuda()
        cudnn.benchmark = True
    #eval dataset
    img_root = '/data/samples/ICDAR/Challenge1_Test_Task12_Images'
    res_root = '/data/houyaozu/textbox/result'
    # txt_root = '/data/samples/ICDAR/Challenge1_Test_Task1_GT'
    # det_path = 'result/det.xml'
    # gt_path = 'result/gt.xml'
    det_img, det_bbox = eval_net(net, img_root, args.cuda, thresh = args.visual_threshold)
    write2txt(res_root, det_img, det_bbox)

if __name__ == '__main__':
    eval_model()
    ##############