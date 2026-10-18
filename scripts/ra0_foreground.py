"""Offline #73 WIN/LOSS foreground experiment. Never imported by the product.

Uses shipped glyph templates, chroma, fixed ROI geometry, separate YOU/suffix
shape evidence and local contrast. No background darkness identity gate.
Parameters are source-independent. DRAW is outside this experiment.
"""
import json
from pathlib import Path
import numpy as np

PARAMETERS = dict(version=3, min_channel=45, win_chroma=15, loss_rg=15,
                  loss_rb=20, grid=.90, word_grid=.85, phase_margin=.05,
                  min_contrast=8, min_coverage=.008, lit_mask='unchanged shipped WIN/LOSS chroma mask')

def cosine(a,b):
    norm=float(np.linalg.norm(a)*np.linalg.norm(b))
    return float(np.dot(a,b)/norm) if norm else 0.

def grid(mask,gx,gy):
    h,w=mask.shape
    xs=np.array([i*w//gx for i in range(gx)],dtype=int)
    ys=np.array([i*h//gy for i in range(gy)],dtype=int)
    counts=np.add.reduceat(np.add.reduceat(mask.astype(np.int32),ys,axis=0),xs,axis=1)
    areas=np.diff(np.r_[ys,h])[:,None]*np.diff(np.r_[xs,w])[None,:]
    return counts/areas

class Foreground:
    def __init__(self,templates):
        obj=json.loads(Path(templates).read_text(encoding='utf-8'))
        self.gx,self.gy=obj['grid_x'],obj['grid_y']
        self.templates={k:np.array(v).reshape(self.gy,self.gx) for k,v in obj['grid_templates'].items()}

    def classify(self,bgr):
        p=PARAMETERS
        # Weighted luminance reaches 65280; int16 overflows even on ordinary glyphs.
        b,g,r=np.moveaxis(bgr.astype(np.int32),-1,0)
        masks={'WIN_lit':(g>120)&(b>120)&(g-r>25)&(b-r>20)&(abs(g-b)<70),
               'LOSS_lit':(r>130)&(r-g>35)&(r-b>45)&(g>50),
               'WIN_dim':(g>p['min_channel'])&(b>p['min_channel'])&(g-r>p['win_chroma'])&(b-r>p['win_chroma'])&(abs(g-b)<70),
               'LOSS_dim':(r>p['min_channel'])&(r-g>p['loss_rg'])&(r-b>p['loss_rb'])&(g>15)}
        gray=(29*b+150*g+77*r)/256.
        scores={};accepted=[]
        h,w=b.shape
        for key,mask in masks.items():
            label=key.split('_')[0]
            # Empty/very faint troughs are rejected, never carried forward here.
            ys,xs=np.where(mask)
            if len(xs)<h*w*p['min_coverage']:
                scores[key]={'accepted':False,'reason':'insufficient_foreground'};continue
            features=grid(mask,self.gx,self.gy)
            final=self.templates['final_'+label.lower()]
            phase=self.templates['phase_'+label.lower()]
            shape=cosine(features.ravel(),final.ravel())
            phase_score=cosine(features.ravel(),phase.ravel())
            # The shipped complete phrase places YOU and WIN/LOSE on opposite sides of the ROI centre.
            half=self.gx//2
            words=[cosine(features[:,:half].ravel(),final[:,:half].ravel()),
                   cosine(features[:,half:].ravel(),final[:,half:].ravel())]
            # Measure geometry from only the expected central glyph area, while
            # the full-ROI fingerprint still penalizes unrelated coloured text.
            central=mask[int(.20*h):int(.80*h),int(.27*w):int(.73*w)]
            yy,xx=np.where(central)
            span=(xx.max()-xx.min()+1)/w if len(xx) else 0
            yspan=(yy.max()-yy.min()+1)/h if len(yy) else 0
            cx=(xx.mean()+int(.27*w))/w if len(xx) else 0
            cy=(yy.mean()+int(.20*h))/h if len(yy) else 0
            area=np.zeros_like(mask);area[int(.20*h):int(.80*h),int(.27*w):int(.73*w)]=True
            fg=gray[mask&area];background=gray[(~mask)&area]
            contrast=float(np.median(fg)-np.median(background)) if len(fg) and len(background) else 0
            ok=(shape>=p['grid'] and min(words)>=p['word_grid'] and shape>=phase_score+p['phase_margin']
                and .22<=span<=.42 and .35<=yspan<=.75 and .38<=cx<=.62 and .32<=cy<=.68
                and contrast>=p['min_contrast'])
            scores[key]=dict(accepted=bool(ok),grid=shape,phase_grid=phase_score,word_grids=words,
                               span=span,yspan=yspan,center=[cx,cy],contrast=contrast,coverage=len(xs)/(h*w))
            if ok and label not in accepted:accepted.append(label)
        return ('FINAL_'+accepted[0] if len(accepted)==1 else None),scores
