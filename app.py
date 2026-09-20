"""SEOUL / MARKET — Streamlit domestic stock dashboard."""
import hmac, html, re, time
from datetime import timedelta
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from data import KIS, APIError, NAMES, now, number, krx_market, demo_history, demo_quote, demo_flow

st.set_page_config(page_title='SEOUL / MARKET',page_icon='◈',layout='wide')
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800&display=swap');
html,body,[class*="css"]{font-family:'Noto Sans KR',sans-serif;}
.stApp{background:#f4f7fb;color:#15243d;}
.block-container{padding-top:2.4rem;max-width:1540px;}
[data-testid="stSidebar"]{background:#101e35;}
[data-testid="stSidebar"] *{color:#e1e9f4;}
[data-testid="stSidebar"] input,[data-testid="stSidebar"] textarea{color:#15243d;}
h1{font-size:2.15rem!important;font-weight:800!important;letter-spacing:-1.4px;}
h3{font-size:1.1rem!important;letter-spacing:-.3px;}
.eyebrow{font-size:11px;color:#008578;letter-spacing:2.5px;font-weight:800;margin-bottom:8px;}
.sub{color:#687990;font-size:14px;margin-bottom:24px;}
[data-testid="stMetric"]{background:white;border:1px solid #e2e8f0;border-radius:16px;padding:20px 22px;box-shadow:0 4px 18px #17263b04;}
[data-testid="stMetricLabel"]{color:#687990;font-size:13px;}
[data-testid="stMetricValue"]{font-size:1.8rem;font-weight:700;}
[data-testid="stVerticalBlockBorderWrapper"]>div{border-radius:16px;}
.stTabs [data-baseweb="tab-list"]{gap:24px;border-bottom:1px solid #dde5ee;margin:18px 0;}
.stTabs [aria-selected="true"]{color:#008578!important;}
.stButton>button[kind="primary"]{background:#008578;border-color:#008578;}
.note{background:#e8f5f2;border-radius:12px;padding:14px 18px;color:#17685f;font-size:13px;}
@media(max-width:700px){.block-container{padding:1.2rem;}h1{font-size:1.65rem!important;}}
</style>''',unsafe_allow_html=True)

def secret(k, default=''):
    try: return st.secrets.get(k,default)
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError): return default

def fmt(v, digits=0):
    return '—' if pd.isna(v) else f'{v:,.{digits}f}'

def chart_style(fig,height=400):
    fig.update_layout(height=height,paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family='Noto Sans KR, sans-serif',color='#63718a',size=12),
        margin=dict(l=12,r=12,t=20,b=12),legend=dict(orientation='h',y=1.1,x=0),
        hovermode='x unified')
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor='#e8edf3',zerolinecolor='#d4dce8')
    return fig

@st.cache_resource(show_spinner=False)
def client(key, password, env): return KIS(key,password,env)

# Cache only non-account data; account responses stay inside the active session.
@st.cache_data(ttl=60,show_spinner=False)
def public_data(key,password,env,kind,code):
    obj = client(key,password,env)
    result = getattr(obj,kind)(code)
    return result,now()

@st.cache_data(ttl=3600,show_spinner=False)
def daily_market(key, market, date): return krx_market(key,market,date)

key, password = str(secret('KIS_APP_KEY')), str(secret('KIS_APP_SECRET'))
env = str(secret('KIS_ENV','prod'))
if env not in ('prod','vps'): st.error('KIS_ENV는 prod 또는 vps여야 합니다.'); st.stop()
with st.sidebar:
    st.markdown('## ◈ SEOUL / MARKET')
    st.caption('나의 국내주식 리서치 데스크')
    st.divider()
    mode = st.radio('데이터 모드',['데모','API 연결'],index=0)
    st.caption('데모는 생성된 예시 데이터입니다.' if mode=='데모' else 'KRX · 한국투자증권 / 조회 전용')
    st.divider()
    st.markdown('**관심 종목**')
    raw = st.text_area('6자리 종목코드 · 쉼표로 구분',value='005930,000660,035420,005380',height=90)
    codes = list(dict.fromkeys(x.strip() for x in raw.replace('\n',',').split(',') if x.strip()))
    valid = [x for x in codes if re.fullmatch(r'\d{6}',x)]
    if len(valid)!=len(codes): st.warning('종목코드는 숫자 6자리로 입력하세요.')
    codes = valid[:8]
    if len(valid)>8: st.info('첫 버전은 관심 종목을 8개까지 조회합니다.')
    if not codes: st.info('종목코드를 하나 이상 입력하세요.'); st.stop()
    code = st.selectbox('분석 종목',codes,format_func=lambda x:f'{NAMES.get(x,x)} · {x}')
    days = st.select_slider('차트 기간',options=[30,90,180,365],value=180,format_func=lambda x:f'{x}일')
    if st.button('↻ 데이터 새로고침',width='stretch'):
        public_data.clear(); daily_market.clear()
        st.session_state.pop('account_result',None)
    st.caption('시세 캐시 60초 · KRX 일별 캐시 1시간\n수동 갱신 / 한국시간(KST)')
    st.divider()
    st.caption('상승은 빨강 · 하락은 파랑\n수급은 순매수 주식 수 기준')

demo = mode=='데모'
if not demo and (not key or not password):
    st.error('Streamlit Secrets에 KIS_APP_KEY와 KIS_APP_SECRET을 설정한 후 다시 실행하세요.')
    st.stop()
if not demo and env=='vps': st.info('모의투자 환경입니다. 일부 시세·수급 API는 지원되지 않을 수 있습니다.')

st.markdown('<div class="eyebrow">YOUR PERSONAL INVESTING WORKSPACE</div>',unsafe_allow_html=True)
st.title('시장을 읽고, 나의 투자를 살피다.')
st.markdown('<div class="sub">시장 흐름부터 종목의 움직임, 투자자 수급과 자산 현황까지 한곳에서 확인하세요.</div>',unsafe_allow_html=True)
if demo: st.info('DEMO · 모든 가격·수급·잔고는 화면 확인을 위한 가상 데이터이며 실제 시장 정보가 아닙니다.')

def fetch(kind, symbol):
    if demo:
        if kind=='quote': return demo_quote(symbol), now()
        if kind=='history': return demo_history(symbol), now()
        if kind=='flow': return demo_flow(symbol), now()
        if kind=='index': return {'price':2748.32 if symbol=='0001' else 862.14,'change':.84 if symbol=='0001' else -.32}, now()
    return public_data(key,password,env,kind,symbol)

def safe(kind,symbol):
    try: return fetch(kind,symbol)
    except (APIError,KeyError,ValueError,TypeError) as e:
        st.warning(str(e) if isinstance(e,APIError) else '응답 형식이 예상과 다릅니다. 해당 API 명세를 확인하세요.')
        return None,None

cols = st.columns(4)
for col,label,sym in [(cols[0],'KOSPI','0001'),(cols[1],'KOSDAQ','1001')]:
    with col:
        idx,stamp=safe('index',sym)
        st.metric(label,fmt(idx['price'],2) if idx else '—',f"{idx['change']:+.2f}%" if idx and pd.notna(idx['change']) else None,delta_color='inverse')
        if stamp: st.caption(f'한국투자증권 · 조회 {stamp:%m.%d %H:%M:%S}')
with cols[2]:
    quote,stamp=safe('quote',code)
    st.metric(NAMES.get(code,code),fmt(quote['price'])+' 원' if quote else '—',f"{quote['change']:+.2f}%" if quote and pd.notna(quote['change']) else None,delta_color='inverse')
    if stamp: st.caption(f'KRX 시세 · 조회 {stamp:%m.%d %H:%M:%S}')
with cols[3]:
    st.metric('선택 종목 거래대금',fmt(quote['value']/1e8,1)+' 억' if quote else '—')
    st.caption('당일 누적 · 조회 시점 값 / 장중 변동 가능')

tabs=st.tabs(['종목 분석','시장 요약','계좌 잔고','연결 안내'])
with tabs[0]:
    left,right=st.columns([2.3,1],gap='large')
    with left:
        with st.container(border=True):
            st.subheader(f'{NAMES.get(code,code)} · 가격과 거래량')
            hist,stamp=safe('history',code)
            if hist is not None and not hist.empty:
                hist=hist.copy()
                for n in (20,60,120): hist[f'MA{n}']=hist.close.rolling(n).mean()
                view=hist[hist.date>=pd.Timestamp(now().date()-timedelta(days=days))]
                fig=make_subplots(rows=2,cols=1,shared_xaxes=True,vertical_spacing=.05,row_heights=[.76,.24])
                fig.add_trace(go.Candlestick(x=view.date,open=view.open,high=view.high,low=view.low,close=view.close,
                    increasing_line_color='#eb6374',decreasing_line_color='#4b84e7',name='수정주가'),row=1,col=1)
                for n,c in [(20,'#009b8e'),(60,'#f2b84a'),(120,'#9274d8')]:
                    fig.add_trace(go.Scatter(x=view.date,y=view[f'MA{n}'],name=f'{n}일 평균',line=dict(color=c,width=1.5)),row=1,col=1)
                fig.add_trace(go.Bar(x=view.date,y=view.volume,name='거래량',marker_color=np.where(view.close>=view.open,'#edb3bb','#b1caf4'),showlegend=False),row=2,col=1)
                fig.update_layout(xaxis_rangeslider_visible=False)
                fig.update_xaxes(rangebreaks=[dict(bounds=['sat','mon'])])
                fig.update_yaxes(title_text='원',row=1,col=1); fig.update_yaxes(title_text='주',row=2,col=1)
                st.plotly_chart(chart_style(fig,440),width='stretch')
                st.caption(f'일봉 · 수정주가 · {view.date.min():%Y.%m.%d}–{view.date.max():%Y.%m.%d} · 이동평균은 앞선 이력 포함 / 휴일 제외 가상 영업일(데모)' if demo else f'일봉 · 수정주가 · 최근 데이터 {hist.date.max():%Y.%m.%d} · 조회 {stamp:%H:%M:%S} KST')
    with right:
        with st.container(border=True):
            st.subheader('관심 종목')
            watch=[]
            for symbol in codes:
                q,_=safe('quote',symbol)
                watch.append({'종목':NAMES.get(symbol,symbol),'코드':symbol,'현재가':q['price'] if q else np.nan,'등락률(%)':q['change'] if q else np.nan})
            st.dataframe(pd.DataFrame(watch),hide_index=True,width='stretch',
                column_config={'현재가':st.column_config.NumberColumn(format='localized'),'등락률(%)':st.column_config.NumberColumn(format='%+.2f')})
            st.caption('사이드바에서 분석 종목을 변경하세요. 미등록 종목은 코드로 표시합니다.')
        with st.container(border=True):
            st.subheader('오늘의 체크포인트')
            if hist is not None and len(hist)>21 and quote:
                mean=hist[hist.date.dt.date<now().date()].tail(20).volume.mean()
                st.metric('거래량 / 직전 20일 평균',fmt(quote['volume']/mean,2)+' 배' if mean>0 else '—')
                st.caption('장중 누적 거래량을 과거 하루 전체와 비교한 참고값입니다.')
                st.write('기준 시각과 거래대금, 수급의 방향을 함께 확인하세요.')
    with st.container(border=True):
        st.subheader('누가 사고, 누가 팔았을까요?')
        flow,stamp=safe('flow',code)
        if flow is not None and not flow.empty:
            count=st.radio('누적 기간',[5,20],format_func=lambda x:f'최근 {x}거래일',horizontal=True)
            tail=flow.tail(count)
            for col,who in zip(st.columns(3),['외국인','기관','개인']):
                val=tail[who].sum(min_count=1)
                col.metric(f'{who} · {len(tail)}거래일 순매수',fmt(val)+' 주')
            fig=go.Figure()
            for who,c in [('외국인','#009b8e'),('기관','#8a70ce'),('개인','#9aa9bc')]:
                fig.add_trace(go.Bar(x=tail.date,y=tail[who],name=who,marker_color=c))
            fig.update_layout(barmode='group')
            st.plotly_chart(chart_style(fig,280),width='stretch')
            st.caption(f'한국투자증권 · 장 종료 후 제공 · 순매수 수량(주) · 최근 데이터 {flow.date.max():%Y.%m.%d} · 확정 여부 미확인, 추후 변경 가능 · 제공된 날짜만 누적')
with tabs[1]:
    st.subheader('시장 전체의 움직임')
    a,b=st.columns([1,2])
    market=a.selectbox('시장',['KOSPI','KOSDAQ'])
    day=b.date_input('KRX 기준일 · 휴장일 또는 미게시일은 결과가 없을 수 있습니다',value=now().date()-timedelta(days=1),max_value=now().date())
    market_df=None
    if demo:
        market_df=pd.DataFrame([{'코드':c,'종목':n,'종가':demo_quote(c)['price'],'등락률(%)':demo_quote(c)['change'],
            '거래량':demo_quote(c)['volume'],'거래대금':demo_quote(c)['value']} for c,n in NAMES.items()])
        st.caption('데모: 6개 가상 종목으로 시장 요약을 구성합니다. 시장·날짜 선택은 API 모드에서 적용됩니다.')
    elif not secret('KRX_API_KEY'): st.info('KRX_API_KEY를 설정하면 시장별 일별매매정보가 표시됩니다.')
    else:
        try: market_df=daily_market(str(secret('KRX_API_KEY')),market,day.strftime('%Y%m%d'))
        except (APIError,KeyError,ValueError) as e: st.warning(str(e) if isinstance(e,APIError) else 'KRX 응답 형식을 확인하세요.')
    if market_df is not None:
        if market_df.empty: st.info('해당 날짜의 게시 데이터가 없습니다. 이전 거래일을 선택하세요.')
        else:
            c1,c2,c3,c4=st.columns(4)
            c1.metric('거래대금 합계',fmt(market_df['거래대금'].sum(min_count=1)/1e12,2)+' 조')
            changes=market_df['등락률(%)']
            c2.metric('상승 종목',f'{(changes>0).sum():,}');c3.metric('하락 종목',f'{(changes<0).sum():,}');c4.metric('보합 종목',f'{(changes==0).sum():,}')
            st.caption(f'KRX 일별매매정보 · 기준일 {day:%Y.%m.%d} · 결측 등락률은 종목 수 집계에서 제외')
            st.markdown('### 거래대금 상위 20종목')
            top=market_df.sort_values('거래대금',ascending=False).head(20).copy()
            top['거래대금(억원)']=top.pop('거래대금')/1e8
            st.dataframe(top,hide_index=True,width='stretch',
                column_config={'거래대금(억원)':st.column_config.NumberColumn(format='localized'),'등락률(%)':st.column_config.NumberColumn(format='%+.2f')})
with tabs[2]:
    st.subheader('내 자산의 현재 위치')
    unlocked=demo
    gate=str(secret('DASHBOARD_PASSWORD'))
    if not demo:
        if not gate: st.info('잔고를 보려면 Secrets에 DASHBOARD_PASSWORD를 먼저 설정하세요.')
        else:
            supplied=st.text_input('잔고 조회 비밀번호',type='password',key='account_password')
            unlocked=hmac.compare_digest(supplied.encode(),gate.encode())
            if supplied and not unlocked: st.warning('비밀번호가 일치하지 않습니다.')
    if unlocked:
        if demo:
            holdings=pd.DataFrame({'종목':['삼성전자','SK하이닉스','NAVER'],'코드':['005930','000660','035420'],
                '수량':[100,20,10],'평균매입가':[70000,170000,200000],'현재가':[74500,182000,193000],
                '평가금액':[7450000,3640000,1930000],'평가손익':[450000,240000,-70000],'수익률(%)':[6.43,7.06,-3.5]})
            summary={'tot_evlu_amt':'18020000','dnca_tot_amt':'5000000','evlu_pfls_smtl_amt':'620000'}
            astamp=now(); ready=True
        else:
            ready=False
            cano,product=str(secret('KIS_CANO')),str(secret('KIS_ACNT_PRDT_CD','01'))
            if not re.fullmatch(r'\d{8}',cano) or not re.fullmatch(r'\d{2}',product):
                st.info('KIS_CANO(계좌 앞 8자리)와 KIS_ACNT_PRDT_CD(뒤 2자리)를 문자열로 설정하세요.')
            elif st.button('계좌 잔고 조회 / 갱신',type='primary'):
                try:
                    st.session_state.account_result=client(key,password,env).balance(cano,product)
                except (APIError,KeyError,ValueError) as e:
                    st.session_state.pop('account_result',None)
                    st.error(str(e) if isinstance(e,APIError) else '잔고 응답 형식을 확인하세요.')
            if 'account_result' in st.session_state:
                holdings,summary,astamp=st.session_state.account_result;ready=True
        if ready:
            a,b,c=st.columns(3)
            a.metric('총 평가금액',fmt(number(summary.get('tot_evlu_amt')))+' 원')
            b.metric('주식 평가손익',fmt(number(summary.get('evlu_pfls_smtl_amt')))+' 원')
            c.metric('예수금',fmt(number(summary.get('dnca_tot_amt')))+' 원')
            st.caption(f'조회 {astamp:%Y.%m.%d %H:%M:%S} KST · 예수금은 주문가능금액과 다를 수 있습니다.')
            if holdings.empty: st.info('보유 중인 국내주식이 없습니다.')
            else:
                a,b=st.columns([2,1])
                a.dataframe(holdings,hide_index=True,width='stretch')
                positive=holdings[holdings['평가금액']>0]
                fig=go.Figure(go.Pie(labels=positive['종목'],values=positive['평가금액'],hole=.72,
                    marker_colors=['#009b8e','#648ae5','#a388d4','#eac36c'],textinfo='percent'))
                b.plotly_chart(chart_style(fig,270),width='stretch')
                b.caption('국내주식 평가금액 기준 비중 · 현금 제외')
            if not demo and st.button('잔고 숨기기'):
                st.session_state.pop('account_result',None)
                st.session_state.pop('account_password',None)
                st.rerun()
    else: st.session_state.pop('account_result',None)
with tabs[3]:
    st.subheader('API 연결 안내')
    st.write('Secrets 설정 후 사이드바에서 API 연결을 선택하세요. 주문 기능은 포함하지 않습니다.')
    st.code('''KRX_API_KEY = "발급받은 KRX 인증키"
KIS_APP_KEY = "한국투자증권 App Key"
KIS_APP_SECRET = "한국투자증권 App Secret"
KIS_ENV = "prod"
KIS_CANO = "12345678"
KIS_ACNT_PRDT_CD = "01"
DASHBOARD_PASSWORD = "충분히-긴-나만의-잔고조회-비밀번호"''',language='toml')
    st.write('KIS_ENV: 실전은 prod, 모의투자는 vps입니다. 키와 계좌도 같은 환경의 값을 사용하세요.')
    st.write('KRX: 유가증권·코스닥 일별매매정보의 이용 승인을 확인하세요. 계좌 정보 없이도 시세 화면을 사용할 수 있습니다.')
    st.write('개인 계좌를 연결하는 배포 앱은 비공개로 운영하세요. 이 잔고 비밀번호는 간단한 추가 잠금이며 사용자별 로그인 기능은 아닙니다.')
    st.markdown('[한국투자증권 API 문서](https://apiportal.koreainvestment.com/) · [KRX OpenAPI](https://openapi.krx.co.kr/)')
st.caption('SEOUL / MARKET  ·  가격·수급·계좌 정보를 함께 보는 개인 리서치 데스크  ·  시간대: Asia/Seoul')
