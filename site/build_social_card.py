"""Compose an English social card from recorded observations and typography."""
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parent

def main():
    serif=next(p for p in [Path('/System/Library/Fonts/Supplemental/Georgia.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf')]if p.exists())
    sans=next(p for p in [Path('/System/Library/Fonts/Supplemental/Arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]if p.exists())
    card=Image.new('RGB',(1600,900),'white');d=ImageDraw.Draw(card)
    def text(x,y,t,size=28,color='#667f8c',face=sans):d.text((x,y),t,font=ImageFont.truetype(str(face),size),fill=color)
    text(64,55,'MIRROR MAZE / A NAVIGATION EXPERIMENT',25)
    text(60,171,'Can AI find its way',73,'#252a2c',serif);text(60,270,'through mirrors?',73,'#466878',serif)
    text(65,425,'Three actions. Reflective walls.',33,'#556e7b',serif);text(65,481,'Every decision is replayable.',33,'#556e7b',serif)
    text(65,624,'44 recorded model runs',29,'#405c6c');text(65,680,'Perception, spatial memory, and planning.',25,'#8a9aa3')
    text(65,798,'Kenny',31,'#445d6b',serif)
    hero=json.loads((ROOT/'data/hero.json').read_text())
    for i,v in enumerate(hero['views']):
        with Image.open(ROOT/v['image'])as im:picture=im.convert('RGB').resize((560,315),Image.Resampling.LANCZOS)
        card.paste(picture,(980,130+i*340));d=ImageDraw.Draw(card);d.rectangle((979,129+i*340,1540,445+i*340),outline='#dfe6e9',width=1)
    text(980,816,'Two views. One physical cell.',19,'#8a9aa3')
    card.save(ROOT/'assets/social-card.png',optimize=True)
    print('SOCIAL_CARD_READY')

if __name__=='__main__':main()
