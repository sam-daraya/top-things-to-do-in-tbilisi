#!/usr/bin/env python3
"""Download openly licensed Wikimedia photos, package site, render 84s video.
Run automatically by GitHub Actions on user's repository. Requires internet.
"""
import io, json, os, re, subprocess, sys, time
from pathlib import Path
from html import unescape
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES=True
ROOT=Path(__file__).resolve().parent
DIST=ROOT/'dist'; ASSETS=DIST/'assets'
ASSETS.mkdir(parents=True,exist_ok=True)
MEDIA=json.loads((ROOT/'MEDIA_MANIFEST.json').read_text(encoding='utf-8'))
UA='TbilisiTravelGuide/1.1 (https://github.com/sam-daraya/top-things-to-do-in-tbilisi) python-requests/2.32'
SESSION=requests.Session();SESSION.headers.update({'User-Agent':UA,'Accept':'application/json, image/*;q=0.9, */*;q=0.1'})

def request(url,params=None,binary=False):
 for attempt in range(5):
  try:
   r=SESSION.get(url,params=params,timeout=45)
   if r.status_code in (429,500,502,503,504):
    print('Retry',r.status_code,r.url,flush=True);time.sleep(2*(attempt+1));continue
   r.raise_for_status();return r
  except requests.RequestException as e:
   print('Retry request',str(e)[:120],flush=True)
   if attempt==4:raise
   time.sleep(2*(attempt+1))
 raise RuntimeError('Wikimedia did not respond after 5 attempts')

def api_info(filename):
 """Use file URLs and CC licenses recorded in MEDIA_MANIFEST.json.

 The live Wikimedia Commons API was returning repeated errors from CI.
 This does not bypass Wikimedia's image CDN; image downloads can still fail
 during a Wikimedia outage, and their original license pages remain in the
 project's MEDIA_CREDITS.md and MEDIA_MANIFEST.json.
 """
 import hashlib
 row = next((entry for entry in MEDIA if entry['filename'] == filename), None)
 if row is None:
  raise ValueError('Missing image from MEDIA_MANIFEST.json: ' + filename)
 url = row.get('img')
 if not url:
  hashpart = hashlib.md5(filename.encode('utf-8')).hexdigest()
  safe_name = quote(filename.replace(' ', '_'), safe=',()')
  url = (f'https://upload.wikimedia.org/wikipedia/commons/thumb/'
         f'{hashpart[0]}/{hashpart[:2]}/{safe_name}/960px-{safe_name}')
 return {'thumburl': url,
         'extmetadata': {'LicenseShortName': {'value': row['license']}}}

def slug(index,title):
 s=re.sub(r'[^a-z0-9]+','-',title.lower()).strip('-')[:65]
 return f'{index:02d}-{s}.webp'

assets={}
for row in MEDIA:
 filename=row['filename'];name=slug(row['index'],row['title'])
 info=api_info(filename)
 md=info.get('extmetadata',{})
 license=unescape(md.get('LicenseShortName',{}).get('value','')).upper()
 if not any(term in license for term in ('CC BY','CC0','PUBLIC DOMAIN','PD-')):
  raise RuntimeError(f'No approved reusable license for {filename}: {license}')
 # cross-check manually verified attribution info supplied in manifest
 print(f"Image {row['index']}: {filename} ({license})",flush=True)
 url=info.get('thumburl') or info['url']
 imgdata=request(url,binary=True).content
 im=Image.open(io.BytesIO(imgdata));im=ImageOps.exif_transpose(im).convert('RGB')
 im.thumbnail((1600,1300),Image.Resampling.LANCZOS)
 im.save(ASSETS/name,'WEBP',quality=84,method=6)
 assets[filename]=name
print('Saved',len(assets),'WebP files')

soup=BeautifulSoup((ROOT/'index.html').read_text(encoding='utf-8'),'html.parser')
for im in soup.select('img[data-original-file]'):
 filename=im.get('data-original-file')
 if filename not in assets:raise RuntimeError(f'HTML image not found in manifest: {filename}')
 im['src']='assets/'+assets[filename]
 im['loading']='lazy'
 im.pop('referrerpolicy',None)
# fullwidth banner
pan=soup.select_one('.panorama-feature-image')
if pan:
 pan['src']='assets/'+assets['Tbilisi_Sunset_Panorama.jpg']
 pan.pop('referrerpolicy',None)
# Create poster from Old Town still
poster=ASSETS/'tbilisi-poster.webp'
photo=Image.open(ASSETS/assets['Old_Tbilisi_Panorama_09.23_(2).jpg'])
posterim=ImageOps.fit(photo,(1280,720),method=Image.Resampling.LANCZOS)
posterim.save(poster,'WEBP',quality=85)
# Build film via FFmpeg, from real still photos (not original motion footage)
shot_ids=[1,2,3,5,8,10,12,4]
clips=[]
for j,i in enumerate(shot_ids):
 row=next(x for x in MEDIA if x['index']==i)
 inpath=ASSETS/assets[row['filename']]
 outpath=ASSETS/f'_scene{j:02}.mp4'
 # 8 × 10.5s = 84 seconds, 24 fps; slow Ken Burns zoom
 vf=("scale=1400:788:force_original_aspect_ratio=increase,"
     "crop=1400:788,"
     "zoompan=z='min(zoom+0.00029,1.077)':d=1:"
     "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
     "s=1280x720:fps=24,format=yuv420p")
 cmd=['ffmpeg','-nostdin','-y','-loglevel','error','-loop','1','-framerate','24','-i',str(inpath),
      '-frames:v','252','-vf',vf,'-r','24','-an','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(outpath)]
 print('Rendering scene',j+1,'of',len(shot_ids),flush=True)
 subprocess.run(cmd,check=True)
 clips.append(outpath)
 listfile=ASSETS/'_concat.txt'
 listfile.write_text(''.join("file '%s'\n" % x.name for x in clips))
 silent=ASSETS/'_silent.mp4'
 subprocess.run(['ffmpeg','-nostdin','-y','-loglevel','error','-f','concat','-safe','0','-i',str(listfile),
   '-c','copy',str(silent)],cwd=ASSETS,check=True)
 final=ASSETS/'tbilisi-in-84-seconds.mp4'
 subprocess.run(['ffmpeg','-nostdin','-y','-loglevel','error','-i',str(silent),
   '-i',str(ROOT/'assets/tbilisi-original-theme.m4a'),'-map','0:v:0','-map','1:a:0',
   '-c:v','copy','-c:a','aac','-b:a','128k','-shortest','-movflags','+faststart',str(final)],check=True)
 for path in clips+[listfile,silent]:path.unlink()
print('Rendered',final,final.stat().st_size,'bytes',flush=True)

# meta/canonical actual public GitHub URL
repo=os.environ.get('GITHUB_REPOSITORY','example/top-things-to-do-in-tbilisi')
owner,repo_name=repo.split('/',1)
site=f'https://{owner}.github.io/{repo_name}/'
meta=soup.new_tag('link',rel='canonical',href=site)
soup.head.append(meta)
for prop,content in [('og:url',site),('og:image',site+'assets/tbilisi-poster.webp')]:
 tag=soup.new_tag('meta',attrs={'property':prop,'content':content})
 soup.head.append(tag)
for script in soup.find_all('script',attrs={'type':'application/ld+json'}):
 try:
  data=json.loads(script.string)
  if data.get('@type')=='BlogPosting':
   data['url']=site
   data['mainEntityOfPage']={'@type':'WebPage','@id':site}
   data['datePublished']='2026-10-27'
   data['dateModified']='2026-10-27'
   data['image']=site+'assets/tbilisi-poster.webp'
   script.string=json.dumps(data,ensure_ascii=False)
 except:pass
video_info={'@context':'https://schema.org','@type':'VideoObject','name':'Tbilisi in 84 Seconds',
'description':'An 84-second photo-based visual guide to Tbilisi sights and experiences.',
'uploadDate':'2026-10-27','duration':'PT1M24S',
'contentUrl':site+'assets/tbilisi-in-84-seconds.mp4','thumbnailUrl':site+'assets/tbilisi-poster.webp'}
js=soup.new_tag('script',attrs={'type':'application/ld+json'});js.string=json.dumps(video_info)
soup.head.append(js)
(DIST/'index.html').write_text('<!DOCTYPE html>\n'+str(soup.html)+'\n','utf-8')
print('Website built for',site,flush=True)
