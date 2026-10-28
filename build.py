#!/usr/bin/env python3
"""Build Tbilisi travel article with credited Pexels and Wikimedia photos and 84s video.
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
UA='TbilisiTravelGuide/1.2 (https://github.com/sam-daraya/top-things-to-do-in-tbilisi) python-requests/2.32'
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
 provider=row.get('provider','wikimedia')
 if provider == 'pexels':
  if row.get('license') != 'Pexels License' or not row.get('source','').startswith('https://www.pexels.com/photo/'):
   raise RuntimeError('Missing Pexels image license/source in manifest: '+filename)
  urls=[(row['img'],'Pexels editorial image'),(row['fallback_img'],'Wikimedia licensed fallback')]
 else:
  license=row.get('license','').upper()
  if not any(x in license for x in ('CC BY','CC0','PUBLIC DOMAIN','PD-')):
   raise RuntimeError('Unapproved Wikimedia license: '+filename)
  # Existing Wikimedia URL is a verified page image; a full-resolution file URL is preferred.
  imgurl=row['img']
  if '/commons/thumb/' in imgurl:
   prefix,namepart=imgurl.split('/commons/thumb/',1)
   imgurl=prefix+'/commons/'+namepart.rsplit('/',1)[0]
  urls=[(imgurl,'Wikimedia original image'),(row['img'],'Wikimedia preview fallback')]
 print(f"Image {row['index']}: {row['title']} — {provider}",flush=True)
 photo=None
 for url,label in urls:
  try:
   imgdata=request(url,binary=True).content
   if len(imgdata)<15000:raise RuntimeError(f'Image payload too small: {len(imgdata)} bytes')
   test=Image.open(io.BytesIO(imgdata));test.load()
   if min(test.size)<650:raise RuntimeError(f'Insufficient native resolution {test.size}')
   photo=ImageOps.exif_transpose(test).convert('RGB')
   print('  fetched',label,photo.size,flush=True)
   break
  except (requests.RequestException, RuntimeError, OSError, ValueError) as e:
   print('  source unavailable:',str(e)[:180],flush=True)
 if photo is None:raise RuntimeError('No usable licensed image for '+filename)
 # 2x-optimized images rather than the former 960px Wikimedia previews.
 photo.thumbnail((2500,2200),Image.Resampling.LANCZOS)
 photo.save(ASSETS/name,'WEBP',quality=90,method=6)
 assets[filename]=name
print('Saved',len(assets),'WebP files',flush=True)

soup=BeautifulSoup((ROOT/'index.html').read_text(encoding='utf-8'),'html.parser')
for im in soup.select('img[data-original-file]'):
 filename=im.get('data-original-file')
 if filename not in assets:raise RuntimeError(f'HTML image not found in manifest: {filename}')
 im['src']='assets/'+assets[filename]
 im['loading']='eager' if 'hero-cover-image' in im.get('class',[]) else 'lazy'
 im.attrs.pop('referrerpolicy',None)
# fullwidth banner
pan=soup.select_one('.panorama-feature-image')
if pan:
 pan['src']='assets/'+assets['Tbilisi_Sunset_Panorama.jpg']
 pan.attrs.pop('referrerpolicy',None)
# Create poster from Old Town still
poster=ASSETS/'tbilisi-poster.webp'
photo=Image.open(ASSETS/assets['Old_Tbilisi_Panorama_09.23_(2).jpg'])
posterim=ImageOps.fit(photo,(1280,720),method=Image.Resampling.LANCZOS)
posterim.save(poster,'WEBP',quality=85)
# Build film via FFmpeg, from real still photos (not original motion footage)
shot_ids=[1,2,3,6,7,10,11,13]
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
video_info={'@context':'https://schema.org','@type':'VideoObject','name':'A Short Video Guide to Tbilisi',
'description':'A short travel video featuring the sights and atmosphere of Tbilisi, Georgia.',
'embedUrl':'https://www.youtube.com/embed/_nXz75uuLbI',
'url':'https://www.youtube.com/watch?v=_nXz75uuLbI',
 'thumbnailUrl':'https://i.ytimg.com/vi/_nXz75uuLbI/hqdefault.jpg'}
js=soup.new_tag('script',attrs={'type':'application/ld+json'});js.string=json.dumps(video_info)
soup.head.append(js)
(DIST/'index.html').write_text('<!DOCTYPE html>\n'+str(soup.html)+'\n','utf-8')
print('Website built for',site,flush=True)
