"""Rebuild the app icon's lion layer from the source artwork.

    /usr/bin/python3 mac_app/make_lion.py 0.72 mac_app/AppIcon.icon/Assets/lion.svg
    ./mac_app/build.sh

Pulls the white lion out of the purple source image, scales it so its bounding
box is the given fraction of the 1024pt icon canvas (0.72 matches the visual
weight of macOS's own icons), and traces it to a crisp SVG with potrace. The
purple background, glass and shadow live in AppIcon.icon/icon.json.
"""
import sys, re, subprocess
from PIL import Image, ImageOps
frac=float(sys.argv[1]); out_svg=sys.argv[2]
src="/Users/edbeesley/Desktop/Building FPL Optimiser/FPL Icon.icon/Assets/a89cc7f74d03a4082c55f817df5f4379.jpg"
im=Image.open(src).convert("L"); bg=(46,9,61); lb=0.2126*bg[0]+0.7152*bg[1]+0.0722*bg[2]
mask=im.point(lambda v:max(0,min(255,int(round((v-lb)/(255-lb)*255)))))
x0,y0,x1,y1=133,74,602,661; pad=6
crop=mask.crop((x0-pad,y0-pad,x1+1+pad,y1+1+pad)); cw,ch=crop.size
C=1024; SS=4; s=frac*C/(y1-y0+1)
big=crop.resize((round(cw*s*SS),round(ch*s*SS)),Image.LANCZOS)
cv=Image.new("L",(C*SS,C*SS),0); cv.paste(big,((C*SS-big.width)//2,(C*SS-big.height)//2))
ImageOps.invert(cv.point(lambda v:255 if v>=128 else 0)).convert("1").save("_t.bmp")
subprocess.run(["potrace","_t.bmp","-s","-o","_t.svg","-r","288","-t","40","-a","0.9","-O","0.4","--color","#ffffff"],check=True)
svg=open("_t.svg").read()
svg=re.sub(r'<\?xml.*?\?>\s*<!DOCTYPE[^>]*>\s*','',svg,flags=re.S)
svg=re.sub(r'<metadata>.*?</metadata>\s*','',svg,flags=re.S)
svg=svg.replace('width="1024.000000pt" height="1024.000000pt"','width="1024" height="1024"')
open(out_svg,"w").write('<?xml version="1.0" encoding="UTF-8"?>\n'+svg)
