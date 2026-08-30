import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from result_detector import _profiles, _x_profile, _y_profile, _grid_profile


def ref_x(mask, width, height, bins):
    out=[]
    for bi in range(bins):
        x0=int(bi*width/bins)
        x1=max(x0+1,int((bi+1)*width/bins))
        c=0
        for y in range(height):
            row=y*width
            for x in range(x0,x1):
                c += 1 if mask[row+x] else 0
        out.append(c/max(1,(x1-x0)*height))
    return out


def ref_y(mask, width, height, bins):
    out=[]
    for bi in range(bins):
        y0=int(bi*height/bins)
        y1=max(y0+1,int((bi+1)*height/bins))
        c=0
        for y in range(y0,y1):
            row=y*width
            for x in range(width):
                c += 1 if mask[row+x] else 0
        out.append(c/max(1,(y1-y0)*width))
    return out


def ref_grid(mask,width,height,gx,gy):
    out=[]
    for byi in range(gy):
        y0=int(byi*height/gy)
        y1=max(y0+1,int((byi+1)*height/gy))
        for bxi in range(gx):
            x0=int(bxi*width/gx)
            x1=max(x0+1,int((bxi+1)*width/gx))
            c=0
            for y in range(y0,y1):
                row=y*width
                for x in range(x0,x1):
                    c += 1 if mask[row+x] else 0
            out.append(c/max(1,(y1-y0)*(x1-x0)))
    return out

rng=random.Random(0xAC6)
for width,height in ((127,41),(320,63),(1489,100)):
    mask=bytearray(rng.getrandbits(1) for _ in range(width*height))
    # Keep the production bin counts and one non-divisible geometry case.
    for bx,by,gx,gy in ((64,16,32,8),(31,11,17,7)):
        ox=_x_profile(mask,width,height,bx)
        oy=_y_profile(mask,width,height,by)
        og=_grid_profile(mask,width,height,gx,gy)
        assert ox == ref_x(mask,width,height,bx)
        assert oy == ref_y(mask,width,height,by)
        assert og == ref_grid(mask,width,height,gx,gy)
        assert _profiles(mask,width,height,bx,by) == ox+oy

print('profile optimization exact-equivalence: OK')
