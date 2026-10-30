"""Render the five authored architecture views as editable SVG and matching PNG.

Requires Pillow. SVG groups retain semantic node and handoff IDs; all labels
remain text. The drawings explain recorded workflows, not current readiness.
"""
from pathlib import Path
from html import escape
from math import atan2, cos, sin, hypot
from functools import lru_cache
import argparse
import json
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'docs/images'
MODELS = json.loads(Path(__file__).with_suffix('.json').read_bytes())
PALETTE = dict(canvas='#F7F9FC', surface='#FFFFFF', ink='#142D40', muted='#526579', border='#DCE5ED', header='#102B3C',
               headerMuted='#B8CAD6', white='#FFFFFF', service='#2764B5', serviceTint='#EEF4FC', store='#AF681A',
               storeTint='#FFF7E9', ml='#7055B5', mlTint='#F4F0FB', data='#127A74', dataTint='#EAF7F3', ops='#536B82',
               opsTint='#EEF2F6', security='#A34E63', securityTint='#FBF0F3', client='#34566F', clientTint='#EDF4F8')


@lru_cache(maxsize=64)
def face(size, bold=False, mono=False):
    """Use installed system fonts without redistributing proprietary font files."""
    filename = 'consola.ttf' if mono else ('seguisb.ttf' if bold else 'segoeui.ttf')
    candidates = [Path('C:/Windows/Fonts')/filename,
                  Path('/usr/share/fonts/truetype/dejavu')/('DejaVuSansMono.ttf' if mono else 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError('Install Segoe UI or DejaVu Sans/Mono to render the diagrams.')


class Canvas:
    """Share geometry, typography and semantic IDs between vector and raster outputs."""
    def __init__(self, model):
        """Create a diagram canvas, accessible SVG description and local drawing inventory."""
        self.model = model
        self.width, self.height = model['width'], model['height']
        self.image = Image.new('RGBA', (self.width, self.height), PALETTE['canvas'])
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" viewBox="0 0 {self.width} {self.height}" role="img" aria-labelledby="title desc">',
                    f'<title id="title">{escape(model["title"])}</title><desc id="desc">{escape(model["subtitle"])}</desc>',
                    '<defs><filter id="card-shadow" x="-10%" y="-20%" width="120%" height="150%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#142D40" flood-opacity="0.07"/></filter></defs>']
        self.text_bounds, self.label_bounds = [], []
        self.rect(0, 0, self.width, self.height, 'canvas')

    def rect(self, x, y, w, h, fill, radius=0, stroke=None, shadow=False):
        """Draw a rounded surface with an optional restrained card shadow."""
        if shadow:
            layer = Image.new('RGBA', (int(w)+32, int(h)+32))
            ImageDraw.Draw(layer).rounded_rectangle((16,16,w+16,h+16), radius=radius, fill=(20,45,64,18))
            self.image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(4)), (int(x)-16,int(y)-13))
            self.draw = ImageDraw.Draw(self.image)
        color, line = PALETTE.get(fill,fill), PALETTE.get(stroke,stroke)
        self.draw.rounded_rectangle((x,y,x+w,y+h), radius=radius, fill=color, outline=line, width=1)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{color}"'+(f' stroke="{line}"' if line else '')+(' filter="url(#card-shadow)"' if shadow else '')+'/>')

    def wrap(self, value, width, size, bold=False, mono=False):
        """Wrap explicit paragraphs at word boundaries using the actual rendering font."""
        rows = []
        for paragraph in value.split('\n'):
            line = ''
            for word in paragraph.split():
                candidate = (line+' '+word).strip()
                if line and self.draw.textlength(candidate, font=face(size,bold,mono)) > width:
                    rows.append(line)
                    line = word
                else:
                    line = candidate
            rows.append(line)
        return rows

    def text(self, x, y, value, width, size=16, tone='muted', bold=False, mono=False, line=None, owner=''):
        """Render editable SVG text and identical raster line breaks with measurable bounds."""
        line = line or int(size*1.35+0.5)
        rows = self.wrap(value,width,size,bold,mono)
        color = PALETTE[tone]
        family = 'Consolas,DejaVu Sans Mono,monospace' if mono else 'Segoe UI,DejaVu Sans,Arial,sans-serif'
        for i, row in enumerate(rows):
            measured = self.draw.textlength(row,font=face(size,bold,mono))
            if measured > width+0.1:
                raise ValueError(f'Text exceeds its column: {owner}: {row}')
            self.draw.text((x,y+i*line),row,font=face(size,bold,mono),fill=color,anchor='lt')
            ascent = face(size,bold,mono).getmetrics()[0]
            # The top anchor and SVG baseline account for the font's real ink bounds.
            top = face(size,bold,mono).getbbox(row or ' ')[1]
            baseline = y+i*line+ascent-top
            self.svg.append(f'<text x="{x}" y="{baseline}" font-family="{family}" font-size="{size}" font-weight="{600 if bold else 400}" fill="{color}">{escape(row)}</text>')
        height = len(rows)*line
        self.text_bounds.append(dict(owner=owner,x=x,y=y,width=width,height=height,rows=rows))
        return height

    def icon(self, role, x, y):
        """Draw small original line icons for workload roles rather than vendor logos."""
        paths = {
            'service': [[(2,3),(22,3),(22,10),(2,10),(2,3)],[(2,14),(22,14),(22,21),(2,21),(2,14)],[(6,6),(7,6)],[(6,17),(7,17)]],
            'store': [[(2,5),(5,2),(19,2),(22,5),(19,8),(5,8),(2,5),(2,19),(5,22),(19,22),(22,19),(22,5)],[(2,12),(5,15),(19,15),(22,12)]],
            'ml': [[(5,5),(19,5),(19,19),(5,19),(5,5)],[(9,9),(15,9),(15,15),(9,15),(9,9)],[(9,1),(9,5)],[(15,1),(15,5)],[(9,19),(9,23)],[(15,19),(15,23)],[(1,9),(5,9)],[(19,9),(23,9)],[(1,15),(5,15)],[(19,15),(23,15)]],
            'data': [[(5,3),(5,21)],[(5,13),(18,6),(18,2)],[(2,3),(8,3)],[(2,21),(8,21)],[(15,2),(21,2)]],
            'ops': [[(1,13),(6,13),(10,3),(15,21),(19,13),(23,13)]],
            'security': [[(12,2),(22,6),(20,15),(16,20),(12,23),(8,20),(4,15),(2,6),(12,2)],[(7,12),(11,16),(17,9)]],
            'client': [[(3,3),(21,3),(21,17),(9,17),(3,22),(3,3)]]}
        color = PALETTE[role]
        self.svg.append(f'<g transform="translate({x} {y})" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">')
        for points in paths[role]:
            self.draw.line([(x+a,y+b) for a,b in points],fill=color,width=2,joint='curve')
            self.svg.append('<polyline points="'+' '.join(f'{a},{b}' for a,b in points)+'"/>')
        self.svg.append('</g>')

    def card(self, node):
        """Place a semantic workload card and reject any text extending beyond its surface."""
        x,y,w,h = [node[k] for k in ['x','y','width','height']]
        compact, tone = node['compact'], node['tone']
        pad, title_size, body_size = (12,17,13) if compact else (16,20,16)
        self.svg.append(f'<g id="node-{node["id"]}" aria-label="{escape(node["title"])}"><title>{escape(node["meta"]+": "+node["title"])}</title>')
        self.rect(x,y,w,h,'surface',12,'border',True)
        self.rect(x,y+14,3,h-28,tone,1)
        at = y+(10 if compact else 14)
        at += self.text(x+pad,at,node['meta'],w-2*pad,10 if compact else 11,tone,mono=True,owner=node['id']+'/meta')+(6 if compact else 9)
        self.icon(tone,x+pad,at)
        title_h = self.text(x+pad+34,at,node['title'],w-2*pad-34,title_size,'ink',True,line=23 if compact else 26,owner=node['id']+'/title')
        at += max(24,title_h)+(7 if compact else 10)
        bottom = at+self.text(x+pad,at,node['body'],w-2*pad,body_size,'muted',line=18 if compact else 22,owner=node['id']+'/body')
        if bottom > y+h-7:
            raise ValueError(f'Card text is clipped: {self.model["name"]}/{node["id"]}: {bottom-y} > {h}')
        self.svg.append('</g>')

    def edge(self, e):
        """Route an explicit rounded arrow and keep its numbered label independently editable."""
        points, color = e['points'], PALETTE[e['tone']]
        path = f'M{points[0][0]} {points[0][1]}'
        for a,b,c in zip(points,points[1:],points[2:]):
            ab,bc = hypot(b[0]-a[0],b[1]-a[1]), hypot(c[0]-b[0],c[1]-b[1])
            r = min(12,ab/2,bc/2)
            u = (b[0]-(b[0]-a[0])*r/ab,b[1]-(b[1]-a[1])*r/ab)
            v = (b[0]+(c[0]-b[0])*r/bc,b[1]+(c[1]-b[1])*r/bc)
            path += f' L{u[0]} {u[1]} Q{b[0]} {b[1]} {v[0]} {v[1]}'
        path += f' L{points[-1][0]} {points[-1][1]}'
        self.svg.append(f'<g id="edge-{e["id"]}" aria-label="{escape(e["label"])}"><path d="{path}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"'+(' stroke-dasharray="5 6"' if e['dashed'] else '')+'/>')
        for a,b in zip(points,points[1:]):
            if e['dashed']:
                distance = hypot(b[0]-a[0],b[1]-a[1])
                for t in range(0,int(distance),11):
                    end = min(t+5,distance)
                    self.draw.line([(a[0]+(b[0]-a[0])*s/distance,a[1]+(b[1]-a[1])*s/distance) for s in [t,end]],fill=color,width=3)
            else:
                self.draw.line([tuple(a),tuple(b)],fill=color,width=3)
        tip,before = points[-1],points[-2]
        angle = atan2(tip[1]-before[1],tip[0]-before[0])
        head = [tip]+[[tip[0]-11*cos(angle+delta),tip[1]-11*sin(angle+delta)] for delta in [-.48,.48]]
        self.draw.polygon([tuple(p) for p in head],fill=color)
        self.svg.append('<polygon points="'+' '.join(f'{x},{y}' for x,y in head)+f'" fill="{color}"/>')
        value = e['id']+'  '+e['label']
        length = self.draw.textlength(value,font=face(12,mono=True))
        x,y = e['labelAt'][0]-length/2-8,e['labelAt'][1]
        self.rect(x,y,length+16,23,'canvas',5)
        self.text(x+8,y+3,value,length+.1,12,e['tone'],mono=True,line=17,owner='edge-'+e['id'])
        self.label_bounds.append(dict(id=e['id'],x=x,y=y,width=length+16,height=23))
        self.svg.append('</g>')

    def render(self):
        """Compose boundaries, cards, handoffs and annotations in their reviewed reading order."""
        m = self.model
        self.rect(0,0,self.width,208,'header')
        self.text(64,34,'IOT MAINTENANCE PLATFORM   /   '+m['number']+' OF 05',self.width-128,12,'headerMuted',mono=True)
        self.text(64,66,m['title'],self.width-128,42,'white',True,line=52)
        self.text(64,132,m['subtitle'],self.width-128,18,'headerMuted',line=25)
        for p in m['panels']:
            self.svg.append(f'<g id="boundary-{p["id"]}">')
            self.rect(p['x'],p['y'],p['w'],p['h'],p['tone']+'Tint',18,'border')
            tag = p['number']
            self.text(p['x']+24,p['y']+22,tag,50,12,p['tone'],mono=True)
            self.text(p['x']+72,p['y']+17,p['title'],p['w']-96,21,'ink',True,line=28)
            self.svg.append('</g>')
        for node in m['nodes']:
            self.card(node)
        for edge in m['edges']:
            self.edge(edge)
        for note in m['notes']:
            at = note['y']
            for value,size,tone,weight in note['lines']:
                at += self.text(note['x'],at,value,note['w'],size,tone,weight=='SemiBold',owner='note')+10
        self.rect(64,self.height-98,self.width-128,1,'border')
        self.text(64,self.height-76,m['footer'],self.width-128,14,'muted',line=20,owner='footer')
        self.text(64,self.height-27,'ARCHITECTURE ILLUSTRATION  /  EDITABLE VECTOR SOURCE',self.width-128,10,'muted',mono=True,owner='caption')
        self.svg.append('</svg>')
        OUT.mkdir(exist_ok=True,parents=True)
        (OUT/(m['name']+'.svg')).write_text('\n'.join(self.svg)+'\n',encoding='utf-8',newline='\n')
        self.image.convert('RGB').save(OUT/(m['name']+'.png'),optimize=True)
        return dict(name=m['name'],width=self.width,height=self.height,nodes=[n['id'] for n in m['nodes']],arrows=[e['id'] for e in m['edges']])


def main():
    """Render selected views and update the diagram inventory without touching native evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--view',action='append',choices=[m['name'] for m in MODELS],help='Render only selected views; default: all five.')
    args = parser.parse_args()
    inventory_path = OUT/'diagram-index.json'
    old = json.loads(inventory_path.read_bytes()) if inventory_path.exists() else dict(views=[])
    rendered = {m['name']:Canvas(m).render() for m in MODELS if not args.view or m['name'] in args.view}
    existing = {v['name']:v for v in old['views']}
    existing.update(rendered)
    inventory = dict(kind='authored-architecture-illustrations',runtime_screenshot=False,source='docs/diagrams/platform.py',
                     views=[existing[m['name']] for m in MODELS if m['name'] in existing])
    inventory_path.write_text(json.dumps(inventory,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'rendered':list(rendered),'views':len(inventory['views']),'native_evidence_modified':False}))


if __name__ == '__main__':
    main()
