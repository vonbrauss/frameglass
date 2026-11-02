"""Draw the existing Frameglass mark as a high-resolution macOS icon."""
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(__file__).resolve().parents[1]
scale=2;image=Image.new('RGBA',(1024*scale,1024*scale));draw=ImageDraw.Draw(image)
def box(values):return tuple(round(v*scale) for v in values)
draw.rounded_rectangle(box((44,44,980,980)),radius=210*scale,fill='#141b23',outline='#35444e',width=3*scale)
mint='#adddce'
draw.rounded_rectangle(box((250,250,774,774)),radius=88*scale,outline=mint,width=24*scale)
draw.rounded_rectangle(box((372,372,652,652)),radius=22*scale,outline=mint,width=22*scale)
draw.line(box((512,180,512,305)),fill=mint,width=24*scale)
draw.line(box((512,719,512,844)),fill=mint,width=24*scale)
image=image.resize((1024,1024),Image.Resampling.LANCZOS)
image.save(root/'assets'/'Frameglass.png')
image.save(root/'assets'/'Frameglass.icns')
