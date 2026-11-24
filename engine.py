import re, unicodedata as u, collections, io
import pandas as pd

J={'ㄹ':'ᆯ','ㄴ':'ᆫ','ㅁ':'ᆷ','ㅆ':'ᆻ'}
def norm(s): return u.normalize('NFD',u.normalize('NFC',str(s)))
def clean(s): return '' if pd.isna(s) else str(s).strip()
def split_variants(s): return [x.strip() for x in re.split(r'\s+/\s+|\n|;',s) if x.strip()]
def dictionary(df):
    df=df.rename(columns=lambda x:str(x).strip()); out=[]
    if not {'표제형 (원형)','변이형'}.issubset(df.columns): raise ValueError('표제형 (원형), 변이형 열이 필요합니다.')
    for i,r in df.iterrows():
        h=clean(r['표제형 (원형)']); v=clean(r['변이형']); note=clean(r.get('참고 사항',''))
        if not h: continue
        status='제외' if ('확신 표현' in note or '제외하고 쟁점' in note) else ('별도' if any(t in note for t in ['따로 보고','별도 제외','보조로 둠']) else '기본')
        out.append(dict(키=f'R{i+2}',ID=clean(r.get('ID','')),차원=clean(r.get('차원','')) or '미분류',유형=clean(r.get('유형','')) or '미분류',표제형=h,변이형=v,근거문헌=clean(r.get('근거 문헌','')),참고사항=note,집계=status))
    return pd.DataFrame(out)

def literal(s):
    return ''.join(r'\s*' if c.isspace() else re.escape(J.get(c,c)) for c in norm(s))

def variant_patterns(v):
    v=v.strip().strip('-').replace('·','/')
    if not v: return []
    if '~' in v:
        a,b=v.split('~',1); b=b.strip();
        if b in ['않다','지 않았다']: return [literal(a.strip())+r'[^.!?。！？\n]{0,180}?'+literal('지 않았')+ '(?:'+literal('다')+'|'+literal('으며')+')',literal(a.strip())+r'[^.!?。！？\n]{0,180}?'+literal('지 않')+'(?:'+literal('다')+'|'+literal('는다')+')']
        return []
    # Explicit surface variants; dictionary notation for optional 으/이 and jamo.
    if v.startswith('아/어 '): return variant_patterns('아 '+v[4:])+variant_patterns('어 '+v[4:])
    if '/' in v: return [p for t in v.split('/') for p in variant_patterns(t)]
    if '(으)' in v or '(이)' in v:
        m=re.search(r'\((으|이)\)',v); return variant_patterns(v[:m.start()]+m.group(1)+v[m.end():])+variant_patterns(v[:m.start()]+v[m.end():])
    forms={v}
    if v.endswith('하다'):
        root=v[:-2]; forms.update(root+x for x in ['한다','함','하며','하는','하고','하여','하였다','했다'])
    elif v.endswith('되다'):
        root=v[:-2]; forms.update(root+x for x in ['된다','됨','되며','되고','되는','되어','된','될'])
    # Handle jamo notation at word start, including 을/은/음을.
    expanded=set(forms)
    for f in forms:
        if f.startswith(('ㄹ','ㄴ','ㅁ')): expanded.add({'ㄹ':'을','ㄴ':'은','ㅁ':'음'}[f[0]]+f[1:])
        if f.startswith('ㅆ'): expanded.add('었'+f[1:]); expanded.add('았'+f[1:])
        if f.startswith('아/어'): expanded.update([f[3:]])
    return [literal(f) for f in expanded if f]

def compile_dict(d):
    rules=[]; warnings=[]
    for row in d.to_dict('records'):
        variants=split_variants(row['변이형'])
        if not variants: variants=[row['표제형']]; warnings.append(row['키']+' : 변이형 없음 — 표제형으로 탐지')
        for v in variants:
            ps=variant_patterns(v)
            if not ps: warnings.append(row['키']+' : 미지원 표기 '+v)
            for p in ps:
                # Avoid matching final syllable of unrelated tokens for free-standing lexical items.
                if re.fullmatch(r'[가-힣]+',v) and len(v)<=3 and not v.endswith(('다','함','됨')): p=r'(?<![\u1100-\u11ff])'+p+r'(?![\u1100-\u11ff])'
                rules.append((p,row,v))
    # longer / more specific matches win at the same start; excluded entries still mask nested generic forms
    rules.sort(key=lambda t:(-len(t[0]), t[1]['키']))
    unique=[]; seen={}
    for p,r,v in rules:
        if p in seen:
            if seen[p]!=r['키']: warnings.append(f"중복 패턴: {v} ({seen[p]}, {r['키']}) — 앞 항목 우선")
            continue
        seen[p]=r['키']; unique.append((p,r,v))
    if not unique: raise ValueError('탐지 가능한 사전 표현이 없습니다.')
    return Matcher(unique),unique,list(dict.fromkeys(warnings))


class Matcher:
    def __init__(self,rules):
        self.trie={}; self.special=[]; self.rules=[]
        for i,(p,_,_) in enumerate(rules):
            rx=re.compile(p); self.rules.append(rx)
            q=p.removeprefix(r'(?<![\u1100-\u11ff])').removesuffix(r'(?![\u1100-\u11ff])')
            q=q.replace(r'\s*','')
            if re.search(r'(?<!\\)[\[\]{}()?*+|.]',q): self.special.append((i,rx)); continue
            q=re.sub(r'\\(.)',r'\1',q); node=self.trie
            for c in q: node=node.setdefault(c,{})
            node.setdefault(None,[]).append(i)
    def finditer(self,s):
        pos=[i for i,c in enumerate(s) if not c.isspace()]; compact=''.join(s[i] for i in pos); candidates=[]
        for j,c in enumerate(compact):
            node=self.trie.get(c); k=j+1
            while node is not None:
                for i in node.get(None,[]):
                    m=self.rules[i].match(s,pos[j])
                    if m and m.end()>m.start(): candidates.append((m.start(),-m.end(),i,m))
                if k==len(compact): break
                node=node.get(compact[k]); k+=1
        for i,rx in self.special:
            for m in rx.finditer(s): candidates.append((m.start(),-m.end(),i,m))
        end=-1
        for start,negend,i,m in sorted(candidates,key=lambda x:x[:3]):
            if start>=end: end=m.end(); yield i,m

def sentences(s):
    # punctuation with decimal protection, no split on bare line wraps
    s=re.sub(r'\s+',' ',s).strip()
    return [x.strip() for x in re.split(r'(?<=[.!?。！？])\s+|(?<=[。！？])(?=\S)',s) if x.strip()]

def read_papers(file,header=False,sheet=0,field='',limit=None):
    name=getattr(file,'name',str(file)); df=pd.read_excel(file,header=0 if header else None,sheet_name=sheet,dtype=str,nrows=limit)
    if df.shape[1]<6: raise ValueError(f'{name}: 최소 6개 열이 필요합니다.')
    df=df.iloc[:,:6].copy(); df.columns=['논문ID','학술지','연도','제목','세부분야','초록']; df['파일']=name
    df['분야']=field or ('인문학' if '인문학' in name else '공학' if '공학' in name else name)
    yr=pd.to_numeric(df['연도'],errors='coerce'); df['시기']=yr.map(lambda y:'2021–2022' if y in [2021,2022] else '2024–2025' if y in [2024,2025] else str(int(y)) if pd.notna(y) else '미상')
    return df

def analyze(papers,d,progress=None):
    rx,rules,warnings=compile_dict(d); docs=[]; hits=[]; ss=[]
    for n,r in enumerate(papers.to_dict('records')):
        abstract=clean(r['초록']); meta={k:clean(v) for k,v in r.items() if k!='초록'}; meta['문서키']=n+1
        sents=sentences(abstract) if abstract else []; base=0; hs=0; separate=0
        for i,s in enumerate(sents,1):
            normalized=norm(s); found=[]
            for idx,m in rx.finditer(normalized):
                _,dr,v=rules[idx]; start=m.start()
                while start>0 and '\u1160'<=normalized[start]<='\u11ff': start-=1
                matched=u.normalize('NFC',normalized[start:m.end()])
                # explicit dictionary exclusions, even if generic pattern is used
                status=dr['집계']; before=u.normalize('NFC',normalized[max(0,m.start()-35):m.start()]); after=u.normalize('NFC',normalized[m.end():m.end()+25]); reason='문맥 평정 필요'
                if '것이다' in matched and re.search(r'(?:어야|아야|해야)\s*할\s*$',u.normalize('NFC',normalized[max(0,start-35):m.end()]).split('것이다')[0]): status='별도'; reason='당위형'
                if '보았다' in matched or '보였다' in matched: status='제외'; reason='과거형'
                if '시사점을 제공' in matched: status='제외'; reason='연구 의의'
                if status=='기본': base+=1
                elif status=='별도': separate+=1
                hit={**meta,'문장번호':i,'문장':s,'탐지표현':matched,'변이형':v,'사전키':dr['키'],'표제형':dr['표제형'],'차원':dr['차원'],'유형':dr['유형'],'집계':status,'평정':'미평정','판정메모':reason,'근거문헌':dr['근거문헌'],'참고사항':dr['참고사항']}; hits.append(hit); found.append(status)
            c=found.count('기본'); hs+=bool(c); ss.append({**meta,'문장번호':i,'문장':s,'기본탐지수':c,'별도탐지수':found.count('별도'),'제외탐지수':found.count('제외')})
        docs.append({**meta,'분석가능':bool(sents),'문장수':len(sents),'어절수':len(abstract.split()),'기본탐지수':base,'별도탐지수':separate,'완화후보문장수':hs})
        if progress and (n%250==0 or n+1==len(papers)): progress((n+1)/len(papers))
    return pd.DataFrame(docs),pd.DataFrame(hits),pd.DataFrame(ss),warnings

def summarize(docs,keys):
    x=docs.groupby(keys,dropna=False).agg(논문수=('문서키','size'),유효초록수=('분석가능','sum'),문장수=('문장수','sum'),어절수=('어절수','sum'),기본탐지수=('기본탐지수','sum'),별도탐지수=('별도탐지수','sum'),완화후보문장수=('완화후보문장수','sum')).reset_index()
    x['100문장당 탐지수']=100*x['기본탐지수']/x['문장수'].replace(0,float('nan')); x['완화후보문장 비율(%)']=100*x['완화후보문장수']/x['문장수'].replace(0,float('nan')); x['1000어절당 탐지수']=1000*x['기본탐지수']/x['어절수'].replace(0,float('nan')); x['논문당 평균']=x['기본탐지수']/x['유효초록수'].replace(0,float('nan')); return x
