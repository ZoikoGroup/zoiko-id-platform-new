import zipfile,re,sys,os,glob,html
src,dst=sys.argv[1],sys.argv[2]
for f in sorted(glob.glob(os.path.join(src,'*.docx'))):
    x=zipfile.ZipFile(f).read('word/document.xml').decode('utf8')
    x=re.sub(r'</w:p>','\n',x); x=re.sub(r'<w:tab/>','\t',x)
    t=html.unescape(re.sub(r'<[^>]+>','',x))
    t=re.sub(r'\n\s*\n+','\n',t)
    out=os.path.join(dst,os.path.basename(f)[:-5]+'.txt')
    open(out,'w',encoding='utf8').write(t)
    print(len(t.split()), os.path.basename(f))
