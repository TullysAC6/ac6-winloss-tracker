"""Dormant, pure positive-only recognition of the player's own S rank (#15-6).

Input is two unscaled BGR/BGRA crops of the same frame of the evidenced native
1920x1080 English Ranked Single waiting screen: the #15-4 header ROI
(80, 40, 560, 100) and the lobby RANK panel ROI (600, 432, 742, 600).

The complete header, judged by the accepted #15-4 decoder on those pixels, is a
necessary context precondition only. It is never rank evidence, and no category
is returned. Rank evidence comes only from the panel: the badge frame, the RANK
label, the S glyph, and a present top-100 place-number row that is never read.
Every other input abstains. This is not OCR, Rating, PLACE or opponent rank.
No capture, state, clock, I/O, persistence or runtime callers are involved.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import zlib

import numpy as np

import match_header

VERSION = "self-rank-s-lobby.v1"
SOURCE = "direct_lobby_rank_panel"
HEADER_SHAPE = match_header.SHAPE
PANEL_SHAPE = (168, 142)

# Panel-ROI geometry, measured on the pinned dev frame: y0, y1, x0, x1.
FRAME_BOX = (33, 155, 11, 133)   # the light 2 px badge frame
LABEL_BOX = (8, 26, 8, 70)       # the "RANK" label and its bullet
S_BOX = (50, 106, 30, 115)       # the S glyph on its badge background
DIGIT_BOX = (106, 140, 30, 115)  # the place-number row: presence only, never read
WINDOW = 3                       # bounded badge-frame registration, in pixels

# Fixed values; scores are not probabilities. After the first validation run the
# glyph statistic changed from an ink-mask F1 to red-channel correlation; 0.95 now
# applies to that new statistic (docs/ISSUE15_6_SELF_RANK_CONTRACT.md).
MIN_FRAME_CONTRAST = 120.0
MAX_FRAME_INNER = 40.0
MIN_LABEL = 0.90
MIN_GLYPH = 0.95                 # zero-mean normalized correlation of the S box
MIN_DIGIT_INK = 200

# Mechanically extracted from the visually reviewed dev crop
# ranks/s_rank/dev-s-matching-panel.png; no validation image contributes.
# tests/test_self_rank.py reproduces both from that asset.
# The red channel of S_BOX, as uint8 bytes:
_S_RED_PACKED = b'c-nQBiC2^7w#I>kc?bz4gd}7h$vh_nG7x4^thQDZwc>~apn%Mf83F-O0RaaP2T(+h6KZ?BwWal1DRUUL=iaXKkKFx5Z`*s<>chthVUZtuKYPFXeP14zP3Q5%d@-GrOlNSoG#WXDMyE62LB2U6h0b6!na!3gHW^Q1(l}feAu55!qLIlI3Wdhxvbh`{hfWI*3JMAi4h{*3AApK@B3d#6<pGqOLPf9uWw1CxxmIU18iC595Xnp$i_Ia#Bm$H|rPHW128Ye#@Yqyp*k7SME}JRfi})fMnM7xB`3xqN%I1j05~)-w7AZ8D+1rbYi?<i=`qHA;>P-fnPR%DVMG~QaFJQB{JT@EfWEwR#EI8y3D3`-#rlyKT68a}76QCTfL@HBAr822fW3d(#6%`c}ZQquck#5Ygn9|dvEU`i%mrF%_E>FPaGN|+v1|1U~@+XwdWH1E+u}DfMCXp$0CYvLXW@havIdZhNwzj6Z)zjhY_W4{cx4Xm9=5*R^wx*iO^Nsb*EoJ+6e^pqdQBx^N3CYA5R4DonC}IqcClH9J#3TwOh0YczR5^L2rN_(b>+8<9*gWl>onEiq;c$5DPLCU8Z*FL9YqB|xeN(b$dvThclAMG~CPW3H|AbP3#O3k%A~GH#Wv~sFuXdN0H#FJpEiE>i)79nc>FMlrx$JfaMA>S0IXk>QU%R`#)93McoQ{gqnO22_MUM+b{RyR|q)>T$9zYZEBnpF@Vcq@pA5go?<pZd%)8lbDT`rds`Auh6x6k8pcXoGm`n(<IDhu;eG7bw99{iU`Qz%q6i^bt3;c#TCD(yg7Q<ELca|U9wJKEd3KpmYO9d5V7;r94^7kYcTTn?Md<MMX(UF!0ltK7LwCJ7Hh>hu}Pq*3V{Hk+GDNWhY4I#bzEfZ74-wAsMO0My&%1z$aGyWQR1?dyRMp<*t#qrI!|VrN&~xm`t4NkpJc|57Lpi^&y|qast-g*zJ?eLk<(?QU;Jd~9uPX{xVpZf<RBYi&4RSy^+wp$_rd;c&U(54@cnE?0ZoiBgRwA}9onLZQ$BVdF9J_{2ma6&Ddj<rVL4`V95B9QHPQYja~`-T8*brskHW+RF0sinHgcYHAvrS{=|8?H+Fz0<|445vQVp{-Q8c2r42xEEbOmk7Wo;4%*thUMPvHyL;cBFLxCdSWG&#R-a+YFcK0Yqaq?=;>h$=p~_&*@ANu7EseFc&2<&~q>{)86p}b9pzttMSY$+GJP{WW%M_IyY;6ueoxaYJJzsp0muE6+)p~upF+BqthYm#rqtQ_@cmkcTRCjeb-4OrzmZqu$a#18ECN4fc9*f1|aJXPJDj0<hk50f~@Lbt}BTl=gy|uNusphP~pwh_YB9Raz5=$ib(6B&`At7O*A!u|&L2*@8hr7AeZf`hY)W+bjL=uh3Vlr7A7T6nt3X6zNh{q&yWQUG=+#U}Ct*+GS6e@`%H5HH|iRAx5QNbbR!m3J-y`jbKY&>Pr#o@3dI)}^UFqsTGIxG|&77-Pj5RWBr6^D;^w0k@)EsYH|l~S2l%!gSd04R(sX;L`&78())zQ%^bq|@b|JKOGPb=w^EC(K%GQall$fk-)@pwY1jSR8>PKXkmq6M)uLS4gBH5wh0!g49$b(&SIj;Ly-mOk`wGkSw#Z(rs^b+a2}g2o#e<hd3E@8jThf9u^iJ6&FuPz$9{I-yHXPJne0*EsZs26-opv6pAHsg#rd)A{reM6pR=U8WA3eM)9-<4>dK`H8nM!I-r%tU~v>C2Ra`13;HK027%%@l5dWAoF1>et*N1=3ZN392xhHV4w4{H)Msc|DA+(37jHjPar*Sh6Qy5B`4N}|Itx-q(&t7*A|AzKlM*qBJn5n1Zl|X+06nMGA+;Ak@=z-wpC2ED3JDIB3R#axi{miKWGpr|E*9CKf+C4bDw9d4L%fj)G!BbEaXiK06YkHE)>iBE3WZ4g2?<a^JQ8NepUWmV6cdHRM@2$OLXr0o1R9x6r^5cmh>DDcu1!cF5fhTQ$|J{JHjmc@%d@`Lnw6fe(I9mfi$x$TCN?@UDk?G*jSdYCz(dgy5hyeYY7K8tu`uLmG%ASp2Q)d6$WtFX+3xUkI32du#`@e`lS!{vC>08s3<2XXaWPTR(UIs76gngTMTa8)0zwXj9MEw@8dRPNb)v^a$HZc=L;{(DC$i)R4m)k_?OrcTP`Az2(o$PnS$XCR6ys!hIV{^9#l<_e7Zl{?W?M}LwMrsoGZHZ9aHt?EJQ~I^LPhrWIDp3E07?d^thCH+?`ZdSc6WDqk<r)CP+fiYY*kgo=~Ji9RGch7aqQU9qsPk1_Lr7?wGD=iOqdL=hJ>I)BcpL73I)29!C*3B4q$MJBw|VmfkfqR`?9&&=eu}mU~sU%4|Jginfu+{Js_C)Jzd_Oo{N`zd%Asn{r#7H-j?$xkMArJaO1G(u()Izjm@G1^Ai;R2})t*Zf|Vv?z!AQG&D3gI5;qH@gjoj?Y#)P+}C@dyR+xQrOUnD-Q5F2SBLxiecm?v;l0XK3^oKE2XS)PpP?9RViJi+CX-TFTtnvB^F0@bhOS*38b-KCw1ZbhMsD4{J3e;n>eZ{mH%3Qq4qh1;9330I(RbzA@U7c@-3NAZ+1P|cI92hvEEbE+#>8S0@JS>RK&dQ_&Umu2_tNn2_3Q9?(7?bz|Ipygn|JPvkB^NF4Gs_A92-NR1EXU%NBakcuid?UZJ>Ogkd4I?xO}bv4m@lY8-u~%5(vpeVltV==7^+1*^XUxjhFgwjEunN@7$S~7$3iVdt_vE^xpkP52hw>-MxF~&YipC;}ee`Pd(_nGIIU?#Qo7DUs4z}p-_;@=W-D!7MqACB$J5ADR4{_iTT2;+=}Y%OV>xn;PdzHJ$Nwr;NHF4x9{A&fA8_b$B!o_CnqNEPd$7%_3YW>$Ad#-H>VyxovbRM(76(rKB;^@kIjJ%AR!SVB>)s!4Aw4RpwgMMwtZ1{tfHc(rm3l|%??M1j*gyA@OP*maby7e8GbkgdE9&P>dCX2*PUniTs(zLA`%G%d?Eptm`ETHNy!i?Jpg42gerBKAt&#vJ!NGlPE=LZ)YjECG_<tTl~<gqs5(_yUEgAZE&sxUhmgtSt8bq_|LKjdn#YZdhb0#i6%!p9Lt`+JiN#^iNMHerEtblaN~J=n0qJxGLq>+#Y&2#V)6<1XNjN+aPawdVi$dj!j#ZBgy?*_C`t|Eye!6Z`Nl6TPN=gceLP{dRxRgnx3Wb<Q2|y)q+Epl3TCG;6{R3)(v$T<m!@<(S6O)PLq{PG|O2OfgyKmmSeEH_h?AvjVMxH8{!GwT$#^FI^3b{h1R7(UrE>9#zW~dB~<eC5yff_9kCt$e=_=E%^bPBWzk$_K#<5YJ%f4(?BH}9W&HBgizRjZW94j~mvQpHl4L?)Lh1h5qXR3?|goLB3VO0D)YG?2W}$cH6DBom=g@B|VG9~VzK+V<kb(&F6w+QN5Nw&w~JusukHVqvOSCR3<n3Z-1FVscBWu3vlnk4KN5PT%M{eAr;n>-A~yq#4rF(=8S;jR6IKT7JH{#3%2r{PD-%wiXsYuFT#o*&$J?l}fn`&h1bVrAnbxt8`kX;6TmT$n&R9pS}F<*5%{JGBVOY0M-N4h(PHyWLPqgdkYAgw5M`r=C==X3m;d1oq&BxtpNi-a<y8k2KUnRhBTq#q+|N|!msaUSJvPBqoF1%D>Kt%LeOBVH7iTbf^%$OX|q`jcqNd_>Sty)R%ho{7vGMSekIjt)X1aLrl|m{)#-Hxok)4w`C@u;?)|T;>od<9&;BhG%m@2<1oGhqfUeEX`4``f?Axu-=`?zs21sx((x;_om`xV5P;so~<@EaUl7D0K-9O#+CKFhhj#vQKy_|vqtsqsv=ObqeII@VuVxGSB!rQm&t8)vh%P%kN-KT`m^zazcGBi336vAk<m{R3Onx>zxE-m@j*WUfm)%-V5!T(B;-`<Nr7Z%r6p7{10L_F0(`jB;6pljfkXqIWq>L>3lFD|dG`BxUl2lwwUDcQNRs3<!-FF(Jqpg;j$!T@Hn*huyQ!O7OKTYmr6#>UpaK7M=Y%Y)F|TBKLf4MqqgBO{Q$QhT)N!Tpt$_4PHs-~Vvr@1PukSf+sLwshYfUGabX@ZrPnzrE<&Q>K9Qfo6b<VW!n&wODg=^YiqY;xm)u@7^yj&Ces?+4tXn|6+P-a%}9zb!6rb_&Qwea8-j{>f*%o5C8f0-LJo_t!-`n{`>OmsQc?uwINNPZb;9tm@Sq}li6g>&d$l!tBOzFn|wd#Us_n$Sl`-QT%4Kt?z<;Xrl#)Sp8!oxPCj__=*g4m=~u6Qoca0ZU*0b)Y_4x^{MYY`KMgp(-m8FqLJ9>d+5&NAT61!8v*ps90|Pf^epp<9uf^r%70~kX;^G2mX=!=c|5<B@*0;9SH&*<co4;-S{_mNm<)w0!29{t3EE8*HHbAqi0jNTj`Gt3A=6hhw&&@3^t@!<`{*|T0`T4ooxw-lImDRO%M4JJ9{J6E{2mk-|@!$Xb>dv83m0Dxa>(UXZH9IdaJ1;jUFK=6ZcBVP^X#L2I@4ug$U4l?oR##UM2sDF#IUpoX<aG&z{Jc27xb*(rv-`HnoIH(6XG}Mkq2dr=0GgYhzpWrQ%WNq*(R=yLn}s?5%AcvOudlAI1O)$O^^+-|(DU<4%kO@?+kd*ulA~4|EM}7xtjx>HhjUR*c6M%VVP2LMV$a%t<Z|DmCqKWPn?s!R`;kbOmlp#1KfbPi_%Qqa<<o1IPn1|p1|v*K==ID@YgTS<ZcdIh$C?Gbm}9YIn2d%Ud)=Pf6aVqU?5|5pD=TYjo0}UOe*e<ar<B(|Yhz<$b94FI>5(h8#;<o}=VzK>Mx;ZBW|+)bFf=TtOq0cGwVI3;@B*}B*WLr)94<e3?!3d<<r}_n@BY-(qeqV(j^DX`>(;HCBiFAE^j+w3Icv}C-BXZbwOTBe?A%O<Cqu8xNH=7_`%H_`Vl*MIumlPU3yZex{`%m-{iO#>4;`thZf^DUUcCnAikmk_h6XNQx^$`cg3kk&-<mV04}Y_3cTrJcVL^U2#Abv@q581w&1Uoe05F&#Jp'
# The RANK label ink inside LABEL_BOX, as packed bits:
_LABEL_PACKED = b'c-muNAPM}CfAHYJgagbE9GZ?Fa%13cWD@3-{J>zx{^OWhdIM1U@z37LKy?oDKgl^WFfcMXcv!L#YdQe3@)S}'


def _decompress(packed):
    return np.frombuffer(zlib.decompress(base64.b85decode(packed)), np.uint8)


def _box_shape(box):
    return box[1] - box[0], box[3] - box[2]


@dataclass(frozen=True)
class SelfRankRecognition:
    self_rank: str | None = None
    status: str = "failed"
    version: str = VERSION
    source: str = SOURCE
    reason: str = "insufficient_evidence"


def _red_ink(pixels):
    pixels = pixels.astype(np.int16)
    red = pixels[:, :, 2]
    return (red >= 150) & (red - np.maximum(pixels[:, :, 0], pixels[:, :, 1]) >= 90)


def _label_ink(pixels):
    pixels = pixels.astype(np.int16)
    low, high = pixels.min(axis=2), pixels.max(axis=2)
    return (low >= 100) & (high - low <= 50)


def _neighborhood(mask):
    padded = np.pad(mask, 1)
    height, width = mask.shape
    return np.logical_or.reduce([padded[y:y + height, x:x + width]
                                 for y in range(3) for x in range(3)])


def _f1(reference, candidate):
    # Mutual ink proximity within one pixel, as in #15-4; missing ink still fails.
    r, c = int(reference.sum()), int(candidate.sum())
    if not r or not c:
        return 0.0
    recall = int((reference & _neighborhood(candidate)).sum()) / r
    precision = int((candidate & _neighborhood(reference)).sum()) / c
    return 2 * recall * precision / (recall + precision) if recall + precision else 0.0


def _window(values, box, dy, dx):
    """The part of ``values`` under ``box`` moved by (dy, dx); outside the crop is zero."""
    y0, y1, x0, x1 = box
    out = np.zeros((y1 - y0, x1 - x0) + values.shape[2:], dtype=values.dtype)
    sy0, sy1 = max(0, y0 + dy), min(values.shape[0], y1 + dy)
    sx0, sx1 = max(0, x0 + dx), min(values.shape[1], x1 + dx)
    if sy0 < sy1 and sx0 < sx1:
        out[sy0 - y0 - dy:sy1 - y0 - dy, sx0 - x0 - dx:sx1 - x0 - dx] = values[sy0:sy1, sx0:sx1]
    return out


def _correlation(reference, candidate):
    """Zero-mean normalized correlation. Gain and offset (a source's tone curve) do
    not change it, so brightness never decides the glyph; the lit presentation is
    required by the header and label stages instead. A flat box scores 0."""
    a = reference.astype(np.float64) - reference.mean()
    b = candidate.astype(np.float64) - candidate.mean()
    norm = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum()) / norm if norm else 0.0


def _frame(luminance, dy, dx):
    y0, y1, x0, x1 = FRAME_BOX
    y0, y1, x0, x1 = y0 + dy, y1 + dy, x0 + dx, x1 + dx
    ring = np.concatenate([luminance[y0:y0 + 2, x0:x1].ravel(), luminance[y1 - 2:y1, x0:x1].ravel(),
                           luminance[y0:y1, x0:x0 + 2].ravel(), luminance[y0:y1, x1 - 2:x1].ravel()])
    inner = np.concatenate([luminance[y0 + 3:y0 + 5, x0 + 3:x1 - 3].ravel(),
                            luminance[y1 - 5:y1 - 3, x0 + 3:x1 - 3].ravel(),
                            luminance[y0 + 3:y1 - 3, x0 + 3:x0 + 5].ravel(),
                            luminance[y0 + 3:y1 - 3, x1 - 5:x1 - 3].ravel()])
    return float(ring.mean()) - float(inner.mean()), float(inner.mean())


def _register(pixels):
    """Locate the dark-interior badge frame within the fixed window, or None."""
    luminance = pixels.astype(np.int32).sum(axis=2)
    best = None
    for dy in range(-WINDOW, WINDOW + 1):
        for dx in range(-WINDOW, WINDOW + 1):
            contrast, inner = _frame(luminance, dy, dx)
            if inner <= MAX_FRAME_INNER and (best is None or contrast > best[0]):
                best = (contrast, dy, dx)
    if best is None or best[0] < MIN_FRAME_CONTRAST:
        return None
    return best[1], best[2]


def _opaque_bgr(roi, shape):
    if (not isinstance(roi, np.ndarray) or roi.dtype != np.uint8 or roi.ndim != 3
            or roi.shape[:2] != shape or roi.shape[2] not in (3, 4)):
        return None, "invalid_geometry_or_format"
    if roi.shape[2] == 4 and not np.all(roi[:, :, 3] == 255):
        return None, "nonopaque_evidence"
    return roi[:, :, :3], None


def recognize_self_rank(header_roi, panel_roi):
    """Recognize direct self-rank S evidence or abstain.

    Positive-only: the result is S or failed. A failed result means these crops
    supply no supported evidence; it never means the player is not S.
    """
    panel, problem = _opaque_bgr(panel_roi, PANEL_SHAPE)
    if problem is None:
        _, problem = _opaque_bgr(header_roi, HEADER_SHAPE)
    if problem is not None:
        return SelfRankRecognition(reason=problem)
    if match_header.recognize_header(header_roi).status != "recognized":
        return SelfRankRecognition(reason="unsupported_context")
    offset = _register(panel)
    if offset is None:
        return SelfRankRecognition(reason="badge_frame_absent")
    dy, dx = offset
    if _f1(_LABEL, _window(_label_ink(panel), LABEL_BOX, dy, dx)) < MIN_LABEL:
        return SelfRankRecognition(reason="rank_label_absent")
    red = panel[:, :, 2]
    glyph = max(_correlation(_S_RED, _window(red, S_BOX, dy + ey, dx + ex))
                for ey in (-1, 0, 1) for ex in (-1, 0, 1))
    if glyph < MIN_GLYPH:
        return SelfRankRecognition(reason="s_glyph_absent")
    if int(_window(_red_ink(panel), DIGIT_BOX, dy, dx).sum()) < MIN_DIGIT_INK:
        return SelfRankRecognition(reason="place_number_absent")
    return SelfRankRecognition("S", "recognized", reason="self_rank_s")


_S_RED = _decompress(_S_RED_PACKED).reshape(_box_shape(S_BOX))
_LABEL = np.unpackbits(_decompress(_LABEL_PACKED))[:np.prod(_box_shape(LABEL_BOX))].reshape(
    _box_shape(LABEL_BOX)).astype(bool)
_LABEL.flags.writeable = False
