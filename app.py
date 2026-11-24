import streamlit as st
import pandas as pd
import io,zipfile
from engine import *
st.set_page_config(page_title='한국어 완화표지 분석실',layout='wide')
st.title('한국어 완화표지 분석실')
st.caption('학술논문 초록 · 사전 기반 탐지 · 문장 단위 분석')
st.info('자동 탐지 결과는 완화 기능이 확인된 정답이 아니라 후보입니다. 능력, 인용, 절차 기술 등은 문맥 평정이 필요합니다.')
with st.sidebar:
    st.header('1. 사전 업로드')
    f=st.file_uploader('완화표지 사전',type=['xlsx','xls'])
    st.header('2. 논문 업로드')
    files=st.file_uploader('논문 엑셀 (복수 선택 가능)',type=['xlsx','xls'],accept_multiple_files=True)
    header=st.checkbox('논문 파일 첫 행에 열 제목이 있음',False)
    dedup=st.checkbox('논문 ID 중복 제거 (빈 ID는 유지)',True)
    sample=st.number_input('파일당 분석 제한 (0=전체)',0,1000000,0,step=100)
if not f: st.stop()
try:
    book=pd.ExcelFile(f); sheet=st.sidebar.selectbox('사전 시트',book.sheet_names,index=next((i for i,n in enumerate(book.sheet_names) if '사본' in n),0)); d=dictionary(pd.read_excel(book,sheet_name=sheet))
except Exception as e: st.error(str(e)); st.stop()
st.subheader('사전 검토 및 집계 설정')
st.caption('기본=주 분석, 별도=민감도 검토, 제외=탐지 기록만 유지. 새 행이나 유형은 업로드 시 자동 반영됩니다. ID가 비어 있어도 행 키로 구별합니다.')
edited=st.data_editor(d,hide_index=True,use_container_width=True,disabled=['키'],column_config={'집계':st.column_config.SelectboxColumn(options=['기본','별도','제외'],required=True)},key='dictionary_'+sheet)
if not files: st.stop()
fields={f.name:st.sidebar.text_input(f'{f.name} 분야',value='인문학' if '인문학' in f.name else '공학' if '공학' in f.name else f.name,key=f.name) for f in files}
if st.button('완화표지 분석 실행',type='primary'):
    try:
        frames=[]
        for f in files:
            f.seek(0); p=read_papers(f,header,field=fields[f.name],limit=int(sample) or None); frames.append(p)
        p=pd.concat(frames,ignore_index=True); original=len(p)
        if dedup: p=p[~(p['논문ID'].notna() & p['논문ID'].ne('') & p['논문ID'].duplicated())]
        if p.empty: raise ValueError('분석할 논문이 없습니다.')
        st.session_state.pop('zip',None)
        bar=st.progress(0,text='문장 분리 및 표현 탐지 중')
        docs,hits,sents,warnings=analyze(p,edited,lambda x:bar.progress(x,text='문장 분리 및 표현 탐지 중')); bar.empty()
        st.session_state['result']=(docs,hits,sents,warnings,edited.copy(),original-len(p))
    except Exception as e: st.exception(e)
if 'result' not in st.session_state: st.stop()
docs,hits,sents,warnings,used,removed=st.session_state['result']; st.caption('아래는 마지막 실행 결과입니다. 사전이나 파일을 변경한 경우 분석을 다시 실행하세요.')
cols=st.columns(4)
for c,label,value in zip(cols,['유효 초록','문장 수','기본 후보 탐지','100문장당 후보'],[int(docs['분석가능'].sum()),int(docs['문장수'].sum()),int(docs['기본탐지수'].sum()),round(100*docs['기본탐지수'].sum()/max(1,docs['문장수'].sum()),2)]): c.metric(label,f'{value:,}')
st.caption(f'중복 제거 {removed:,}행 · 빈 초록 {(~docs["분석가능"]).sum():,}행 · 문장 분리는 구두점 기반 근사 방식')
tabs=st.tabs(['분야·시기 비교','표현·유형별 빈도','문장 확인 및 평정용 표본','논문별 결과','내려받기 및 규칙'])
with tabs[0]:
    summary=summarize(docs,['분야','시기']); st.dataframe(summary,hide_index=True); st.bar_chart(summary.pivot(index='분야',columns='시기',values='100문장당 탐지수')); st.dataframe(summarize(docs,['분야','세부분야','연도']),hide_index=True)
with tabs[1]:
    if not hits.empty:
        groups=hits.groupby(['분야','시기','차원','유형','표제형','집계']).size().rename('빈도').reset_index(); groups=groups.merge(summary[['분야','시기','문장수']],on=['분야','시기']); groups['100문장당 빈도']=100*groups['빈도']/groups['문장수'].replace(0,float('nan')); st.dataframe(groups,hide_index=True)
        top=hits[hits['집계']=='기본']['표제형'].value_counts().head(25); st.bar_chart(top)
    else: st.write('탐지된 표현 없음')
with tabs[2]:
    if not hits.empty:
        h=hits.copy(); chosen=st.multiselect('표제형 필터',sorted(h['표제형'].unique())); status=st.multiselect('집계 상태',['기본','별도','제외'],default=['기본','별도']); h=h[h['집계'].isin(status)]; h=h[h['표제형'].isin(chosen)] if chosen else h
        query=st.text_input('문장 검색'); h=h[h['문장'].str.contains(query,regex=False)] if query else h
        n=st.number_input('표본 크기',1,10000,100); seed=st.number_input('난수 시드',0,999999,42); subset=h.sample(min(len(h),n),random_state=int(seed)); st.dataframe(h.head(1000),hide_index=True); st.download_button('무작위 평정 표본 CSV',subset.to_csv(index=False).encode('utf-8-sig'),'평정표본.csv')
    else: st.write('탐지된 표현 없음')
with tabs[3]: st.dataframe(docs,hide_index=True)
with tabs[4]:
    st.write('기본 집계는 자동 후보 기준이며, 별도·제외는 분리합니다. 문장당 여러 표현은 각각 집계하고, 같은 시작 위치에서는 긴 패턴 우선, 겹치는 표현은 한 번만 탐지합니다. 사전의 참고 사항은 대부분 자동 실행 규칙이 아니라 평정 안내입니다.')
    st.write('패턴 충돌 및 미지원 표기'); st.write(warnings or ['없음'])
    if st.button('전체 결과 ZIP 생성'):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for name,frame in [('논문별',docs),('탐지표현별',hits),('전체문장별',sents),('분야시기별',summary),('사용사전',used)]: z.writestr(name+'.csv',frame.to_csv(index=False).encode('utf-8-sig'))
            if not hits.empty: z.writestr('표현유형별.csv',groups.to_csv(index=False).encode('utf-8-sig'))
            z.writestr('규칙경고.txt','\n'.join(warnings))
        st.session_state['zip']=buf.getvalue()
    if 'zip' in st.session_state: st.download_button('전체 결과 ZIP 다운로드',st.session_state['zip'],'완화표지_분석결과.zip',mime='application/zip')
