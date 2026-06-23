import os,sys,csv,math,gzip,base64,json
import numpy as np
from PIL import Image, ImageDraw
import shapefile
from shapely.geometry import shape as shp_shape, box, LineString, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

PROJ="/sessions/clever-compassionate-newton/mnt/Relocation Screening/"
BD="/sessions/clever-compassionate-newton/.local/lib/python3.10/site-packages/mpl_toolkits/basemap_data"
OUT="/sessions/clever-compassionate-newton/mnt/outputs/"

D2R=math.pi/180
phi1,phi2,lon0,lat0=29.5*D2R,45.5*D2R,-96*D2R,23*D2R
aN=0.5*(math.sin(phi1)+math.sin(phi2))
aC=math.cos(phi1)**2+2*aN*math.sin(phi1)
rho0=math.sqrt(aC-2*aN*math.sin(lat0))/aN
def albers(lon,lat):
    lam=lon*D2R; phi=lat*D2R
    rho=math.sqrt(max(0,aC-2*aN*math.sin(phi)))/aN
    th=aN*(lam-lon0)
    return rho*math.sin(th), rho0-rho*math.cos(th)

LATMIN,LATMAX,LONMIN,LONMAX=24,50,-125,-66
BBOX=box(LONMIN,LATMIN,LONMAX,LATMAX)

# ---- MB from snapshot (3-dp lat/lon, as embedded) ----
minx=1e9;maxx=-1e9;miny=1e9;maxy=-1e9
for row in csv.DictReader(open(PROJ+"output/candidate_scores.csv")):
    try: lat=float(row['lat']); lon=float(row['lon'])
    except: continue
    if LATMIN<=lat<=LATMAX and LONMIN<=lon<=LONMAX:
        x,y=albers(lon,lat)
        minx=min(minx,x);maxx=max(maxx,x);miny=min(miny,y);maxy=max(maxy,y)
spanx=maxx-minx; spany=maxy-miny; aspect=spanx/spany
print("MB",round(minx,3),round(maxx,3),round(miny,3),round(maxy,3),"aspect",round(aspect,3))

def read_segments(name,res):
    meta=open(os.path.join(BD,"%smeta_%s.dat"%(name,res))).read().split('\n')
    dat=open(os.path.join(BD,"%s_%s.dat"%(name,res)),'rb').read()
    out=[]
    for line in meta:
        ls=line.split()
        if len(ls)<7: continue
        npts=int(ls[2]); off=int(ls[5]); nb=int(ls[6])
        arr=np.frombuffer(dat,dtype='<f4',count=nb//4,offset=off).reshape(-1,2)
        out.append((ls[0],ls[1],arr))
    return out

def emit_line(geom,tol,minlen,out):
    if geom.is_empty: return
    t=geom.geom_type
    if t=='LineString':
        s=geom.simplify(tol)
        if s.length>minlen and len(s.coords)>=2:
            out.append([[round(x,3),round(y,3)] for x,y in s.coords])
    elif t in ('MultiLineString','GeometryCollection','MultiPolygon','MultiPoint'):
        for g in geom.geoms: emit_line(g,tol,minlen,out)
    elif t=='Polygon':
        emit_line(geom.boundary,tol,minlen,out)
def clip_geom(geom,clipgeom,tol,minlen):
    if not geom.intersects(clipgeom): return []
    out=[]; emit_line(geom.intersection(clipgeom),tol,minlen,out); return out
def poly_rings(geom,tol):
    out=[]
    def add(g):
        if g.is_empty: return
        if g.geom_type=='Polygon':
            s=g.simplify(tol)
            if not s.is_empty: out.append([[round(x,3),round(y,3)] for x,y in s.exterior.coords])
        elif g.geom_type in ('MultiPolygon','GeometryCollection'):
            for gg in g.geoms: add(gg)
    add(geom); return out

# ---- GSHHS intermediate: land polys (for relief mask) + lakes; coastline built later ----
land_polys=[]; lake_polys=[]; l1_orig=[]
for level,area,arr in read_segments('gshhs','i'):
    if len(arr)<4: continue
    try: pg=Polygon(arr)
    except: continue
    if not pg.is_valid: pg=pg.buffer(0)
    if not pg.intersects(BBOX): continue
    if level=='1':
        l1_orig.append(pg)
        land_polys.append(pg.intersection(BBOX))
    elif level=='2':
        try:
            if float(area)<120: continue
        except: pass
        lake_polys.append(pg.intersection(BBOX))
print("land polys",len(land_polys),"| cand lakes",len(lake_polys))

# ---- STATE BORDERS (TIGER) dissolved, clipped to land (drops coast/bay-crossing) ----
EXCL={'02','15','60','66','69','72','78'}
sf=shapefile.Reader(PROJ+"data/raw/tl_2023_us_county/tl_2023_us_county")
si=[f[0] for f in sf.fields[1:]].index('STATEFP')
bystate={}
for srec in sf.iterShapeRecords():
    st=srec.record[si]
    if st in EXCL: continue
    try: g=shp_shape(srec.shape.__geo_interface__)
    except: continue
    if not g.is_valid: g=g.buffer(0)
    bystate.setdefault(st,[]).append(g)
state_unions=[unary_union(gs) for gs in bystate.values()]
US_LAND=unary_union(state_unions).buffer(0)
US_RC=US_LAND.simplify(0.02)
US_CLIP=US_LAND.buffer(0.05)        # clip all map layers to the lower-48 (+ small margin)
US_IN=US_RC.buffer(-0.03)           # interior, to drop coastal/national edges from state borders
borders_line=unary_union([u.simplify(0.02).boundary for u in state_unions])

# state borders: interstate lines only, kept inside the US
states=[]
for u in state_unions:
    states+=clip_geom(u.boundary,US_IN,0.006,0.04)
print("state border lines",len(states),"pts",sum(len(s) for s in states))

# split lakes: border-adjacent (Great Lakes etc.) render as water; interior lakes stay land
border_lakes=[]; interior_lakes=[]
for pg in lake_polys:
    (border_lakes if pg.distance(borders_line)<0.03 else interior_lakes).append(pg)

# coastline: US ocean coast + bays (level-1 boundary) + border-lake shores, clipped to the US
coast=[]
for pg in l1_orig:
    coast+=clip_geom(pg.boundary,US_CLIP,0.0035,0.05)
for pg in border_lakes:
    coast+=clip_geom(pg.boundary,US_CLIP,0.01,0.05)
print("coast lines",len(coast),"pts",sum(len(s) for s in coast))

# national land borders (US–Canada / US–Mexico): the US outline adjacent to real foreign LAND.
# Buffer the US outward first so the TIGER/GSHHS coastline mismatch (thin slivers along the
# coast) and offshore water detail (Puget Sound islands, Florida keys, …) are excluded — only
# the true inland borders remain. Densify so straight-in-lon/lat spans (49th parallel) render
# as proper Albers curves; simplify first to drop the Rio Grande's heavy meander.
GLAND=unary_union([p for p in land_polys if not p.is_empty]).buffer(0)
FOREIGN=GLAND.difference(US_LAND.buffer(0.1))
def densify(coords,step=0.3):
    out=[coords[0]]
    for a,b in zip(coords,coords[1:]):
        n=max(1,int(math.hypot(b[0]-a[0],b[1]-a[1])/step))
        for k in range(1,n+1): out.append((a[0]+(b[0]-a[0])*k/n,a[1]+(b[1]-a[1])*k/n))
    return out
natl=[]
def collect_natl(g):
    if g.is_empty: return
    if g.geom_type=='LineString':
        if g.length>0.08: natl.append([[round(x,3),round(y,3)] for x,y in densify(list(g.simplify(0.01).coords))])
    elif g.geom_type in ('MultiLineString','GeometryCollection'):
        for gg in g.geoms: collect_natl(gg)
collect_natl(US_LAND.boundary.intersection(FOREIGN.buffer(0.12)))
print("national border lines",len(natl),"pts",sum(len(s) for s in natl))

# ---- rivers (low res, major) cropped to US land ----
rivers=[]
for level,area,arr in read_segments('rivers','l'):
    if len(arr)<2: continue
    rivers+=clip_geom(LineString(arr),US_RC,0.02,0.18)
print("rivers",len(rivers),"pts",sum(len(s) for s in rivers))

# ---- Interstate highways (TIGER primary roads RTTYP=I) ----
roads=[]
rsf=shapefile.Reader(PROJ+"data/raw/tl_2023_us_primaryroads/tl_2023_us_primaryroads")
ri=[f[0] for f in rsf.fields[1:]].index('RTTYP')
for srec in rsf.iterShapeRecords():
    if srec.record[ri]!='I': continue
    pts=srec.shape.points; parts=list(srec.shape.parts)+[len(pts)]
    for k in range(len(parts)-1):
        seg=pts[parts[k]:parts[k+1]]
        if len(seg)<2: continue
        roads+=clip_geom(LineString(seg),US_CLIP,0.008,0.03)
print("interstate lines",len(roads),"pts",sum(len(s) for s in roads))

# ---- RELIEF raster: land = shaded relief; everything outside the lower-48 = transparent ----
RW=1400; RH=int(round(RW/aspect))
def to_px(lon,lat):
    x,y=albers(lon,lat); return ((x-minx)/spanx*RW,(maxy-y)/spany*RH)
def rasterize(geoms):
    im=Image.new('L',(RW,RH),0); d=ImageDraw.Draw(im)
    def fill(g):
        if g.geom_type=='Polygon': d.polygon([to_px(x,y) for x,y in g.exterior.coords],fill=255)
        elif g.geom_type in ('MultiPolygon','GeometryCollection'):
            for gg in g.geoms: fill(gg)
    for g in geoms:
        if not g.is_empty: fill(g)
    return np.array(im)>0
gmask=rasterize(land_polys)        # GSHHS land (continent; does NOT cut out the Great Lakes)
usmask=rasterize([US_LAND])        # lower-48 land (TIGER; includes bays + lakes)
imask=rasterize(interior_lakes)    # small interior lakes -> keep as land
blmask=rasterize(border_lakes)     # Great Lakes etc. -> force to water
landmask=usmask & (gmask | imask) & (~blmask)   # US land, minus ocean/bays/Great-Lakes, plus interior lakes
src=Image.open(os.path.join(BD,"shadedrelief.jpg")).convert('RGB').resize((5400,2700))
S=np.asarray(src); SH,SW,_=S.shape
js,is_=np.meshgrid(np.arange(RW),np.arange(RH))
X=minx+(js+0.5)/RW*spanx; Y=maxy-(is_+0.5)/RH*spany
rho=np.sqrt(X**2+(rho0-Y)**2); th=np.arctan2(X,(rho0-Y))
val=np.clip((aC-(rho*aN)**2)/(2*aN),-1,1)
lat=np.arcsin(val)/D2R; lon=(lon0+th/aN)/D2R
col=np.clip(((lon+180)/360*SW).astype(int),0,SW-1)
rowi=np.clip(((90-lat)/180*SH).astype(int),0,SH-1)
samp=S[rowi,col]
gray=(0.299*samp[...,0]+0.587*samp[...,1]+0.114*samp[...,2])[...,None]
land_rgb=np.clip(255-(255-(samp*0.5+gray*0.5))*0.32,0,255)
# Fully OPAQUE image (no alpha channel — avoids Safari's transparent-PNG canvas issues):
# land = shaded relief, everything else = the EXACT water color the app paints as its
# background, so the relief rectangle's edge is invisible and panning never shows a seam.
WATER=np.array([221,232,243],dtype=np.uint8)   # must equal the app's water fill (#dde8f3)
rgb=np.where(landmask[...,None],land_rgb.astype(np.uint8),WATER).astype(np.uint8)
ASSETS=PROJ+"assets/"; os.makedirs(ASSETS,exist_ok=True)
Image.fromarray(rgb,'RGB').save(ASSETS+"relief.png")
print("relief",RW,"x",RH,"->",round(os.path.getsize(ASSETS+'relief.png')/1024),"KB")

bm={"mb":[minx,maxx,miny,maxy],"states":states,"natl":natl,"rivers":rivers,"roads":roads,"coast":coast}
js_json=json.dumps(bm,separators=(',',':'))
open(ASSETS+"basemap.json","w").write(js_json)
print("assets/basemap.json KB",round(os.path.getsize(ASSETS+'basemap.json')/1024),"| assets/relief.png KB",round(os.path.getsize(ASSETS+'relief.png')/1024))
