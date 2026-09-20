# SEOUL / MARKET — 국내주식 대시보드

Python 3.11 이상 / Streamlit. 진입 파일은 `app.py`입니다.
밝은 배경, 남색 사이드바, 청록색 포인트의 개인용 조회 대시보드입니다.

## 설치와 실행

ZIP 압축을 풀고 `stock_dashboard` 폴더에서 실행합니다.

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

처음에는 키 없이 데모 모드로 시작합니다. 사이드바에서 `API 연결`을 선택하면 실제 데이터를 조회합니다.
데모 숫자는 실제 가격이 아니며, API 오류 때 데모로 자동 대체하지 않습니다.

## Streamlit Community Cloud

`app.py`, `data.py`, `requirements.txt`, `.streamlit/config.toml`을 같은 저장소에 올립니다.
앱 진입 파일을 `app.py`로 지정합니다. 폴더째 올렸다면 `stock_dashboard/app.py`를 지정합니다.
앱의 Secrets 편집 영역에 아래 내용을 붙여 넣고 값을 교체하세요.
로컬에서는 같은 내용을 `.streamlit/secrets.toml`로 저장합니다.
실제 secrets.toml은 GitHub에 올리지 마세요. ZIP에는 비어 있는 예시만 포함합니다.

```toml
KRX_API_KEY = "발급받은 KRX 인증키"
KIS_APP_KEY = "한국투자증권 App Key"
KIS_APP_SECRET = "한국투자증권 App Secret"
KIS_ENV = "prod"
KIS_CANO = "12345678"
KIS_ACNT_PRDT_CD = "01"
DASHBOARD_PASSWORD = "나만의-충분히-긴-잔고조회-비밀번호"
```

- `KRX_API_KEY`: KRX 인증키. 유가증권/코스닥 일별매매정보 이용 승인을 확인합니다.
- `KIS_APP_KEY`, `KIS_APP_SECRET`: 같은 환경에서 발급한 키 쌍입니다.
- `KIS_ENV`: 실전 `prod`, 모의 `vps`. 모의에서는 일부 조회 서비스가 제한될 수 있습니다.
- `KIS_CANO`: 계좌 앞 8자리. 숫자처럼 보여도 반드시 따옴표를 붙입니다.
- `KIS_ACNT_PRDT_CD`: 계좌 뒤 상품코드 2자리. 본인 계좌에 맞게 입력합니다.
- `DASHBOARD_PASSWORD`: 사용자가 정하는 잔고 화면 잠금 비밀번호입니다. 증권사 계좌 비밀번호가 아닙니다.

계좌 설정은 잔고 조회에만 필요합니다. 키/계좌 비밀번호를 채팅이나 소스에 붙여 넣지 않아도 됩니다.
계좌 연결 앱은 비공개로 운영하세요. 앱의 비밀번호는 추가 잠금이며 다중 사용자 인증 서비스가 아닙니다.

## 포함 기능

1. KOSPI/KOSDAQ 지수와 선택 종목 현재가·등락률·거래대금
2. 관심 종목 최대 8개, 종목코드 입력 및 선택
3. 30/90/180/365일 캔들·거래량 차트, 20/60/120일 이동평균
4. 외국인/기관/개인 순매수 수량, 최근 제공 5/20거래일 누적
5. KRX 시장별 기준일 거래대금·상승/하락/보합 수와 거래대금 상위 20종목
6. 비밀번호 확인 후 별도 버튼으로 계좌 잔고 조회, 보유 주식 비중

## 데이터 해석 및 동작

- KIS 시세 시장 코드는 J(KRX)이며 NXT 통합 시세가 아닙니다.
- 시세는 REST 조회 스냅샷입니다. 자동 실시간 스트리밍은 포함하지 않습니다.
- 시세 캐시 60초, KRX 일별 데이터 캐시 1시간. 수동 새로고침 버튼으로 갱신합니다.
- 조회 시각은 한국시간입니다. 조회 시각이 마지막 체결 시각을 뜻하지는 않습니다.
- KRX 날짜는 사용자가 지정합니다. 주말/휴일/미게시일은 이전 거래일을 선택하세요.
- 투자자 수급은 장 종료 후 제공되는 API이며 당일 장중 실시간 수급이 아닙니다.
- 수급 단위는 금액이 아닌 주식 수입니다. 제공된 날짜만 합산합니다.
- 일봉은 수정주가입니다. API의 한 번 조회 건수 제한에 맞춰 날짜를 이동하며 이력을 조회합니다.
- 잔고는 연속 조회를 처리합니다. 전체 조회가 실패하면 일부 잔고를 전체처럼 표시하지 않습니다.
- 잔고는 전역 데이터 캐시에 저장하지 않고 현재 사용자 세션에만 보관합니다.
- 주문·자동매매 기능은 없습니다.
- 계좌 예수금은 주문가능금액과 다릅니다. 비중 차트는 국내주식 평가금액만 포함합니다.
- 미등록 종목 이름은 6자리 코드로 표시합니다. 기본 이름 사전은 data.py의 NAMES입니다.
- 관심 종목은 현재 세션에서 관리하며 서버 재시작 후 영구 보존하지 않습니다.
- 한 서버 프로세스에서 토큰 재사용과 호출 간격을 관리합니다. 동일 키를 여러 앱에서 사용하면 별도 호출 제한이 발생할 수 있습니다.
- 최초 이력 조회는 수 초 이상 걸릴 수 있습니다. 오류 코드는 화면에 표시하고 원문 응답/키는 출력하지 않습니다.

## 검증

Streamlit AppTest로 데모 실행·종목 변경·수급 기간 변경·키 누락 처리를 검사했습니다.
단위 테스트는 결측값 처리, KRX 빈 데이터, 주가 이력 날짜 이동, 잔고 연속조회/잘못된 커서 처리를 검사합니다.

```bash
python -m unittest test_data.py
```

브라우저 설치의 네트워크 제한으로 픽셀 단위 화면 검사는 완료하지 못했습니다.
실제 사용자 API 키는 전달받지 않았으므로 실계좌/실시세 인증과 사용자별 서비스 권한은 아직 검증되지 않았습니다.

## 공식 참고

- https://apiportal.koreainvestment.com/
- https://github.com/koreainvestment/open-trading-api
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor/inquire_investor.py
- https://openapi.krx.co.kr/
