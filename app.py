"""이종완의 주식 분석 대시보드 — Python 3.10+ / Streamlit.

설치: python -m pip install -r requirements.txt
실행: python -m streamlit run app.py
API 키는 코드에 적지 않습니다. 다음 중 한 곳에 KRX_API_KEY, DART_API_KEY를 설정하세요.
  · 로컬: 프로젝트 폴더의 .env 파일 또는 .streamlit/secrets.toml (둘 다 .gitignore로 제외)
  · Streamlit Community Cloud: 앱 설정(Settings) → Secrets
공식 KRX OpenAPI와 OpenDART만 사용하며, 조회 실패를 예시 값으로 대체하지 않습니다.
"""
from __future__ import annotations

import hashlib
import html
import io
import math
import os
import re
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

try:  # 로컬 개발용: .env 파일이 있으면 환경변수로 읽어들임 (배포 환경에는 .env가 없음)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TITLE = "이종완의 주식 분석 대시보드"
KST = timezone(timedelta(hours=9))
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
DART_BASE = "https://opendart.fss.or.kr/api"
KRX_SERVICES = {
    "KOSPI 주식": "sto/stk_bydd_trd",
    "KOSDAQ 주식": "sto/ksq_bydd_trd",
    "KOSPI 지수": "idx/kospi_dd_trd",
    "KOSDAQ 지수": "idx/kosdaq_dd_trd",
}
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
RED, BLUE, MINT, PURPLE = "#ff6675", "#5a9bff", "#66e5c3", "#b697ff"
BG, PANEL, TEXT, MUTED = "#0b1222", "#141f34", "#eef3ff", "#9cacc7"
DART_ERRORS = {
    "010": "등록되지 않은 인증키입니다. OpenDART 인증키를 확인하세요.",
    "011": "사용이 중지된 인증키입니다. OpenDART 인증키 관리에서 확인하세요.",
    "012": "허용되지 않은 IP입니다. 실행하는 PC/서버의 IP 등록을 확인하세요.",
    "013": "해당 기간에 제공되는 자료가 없습니다.",
    "014": "해당 원본 파일이 없습니다.",
    "020": "호출 한도를 초과했습니다. 반복 조회를 멈추고 이용 한도를 확인하세요.",
    "021": "동시 조회 가능한 회사 수를 초과했습니다.",
    "100": "요청 인자가 올바르지 않습니다.",
    "101": "허용되지 않은 API 접근입니다.",
    "800": "OpenDART 시스템 점검 중입니다. 나중에 다시 조회하세요.",
    "900": "OpenDART에서 처리되지 않은 오류가 발생했습니다.",
    "901": "계정 개인정보 보유기간이 만료됐습니다. OpenDART에서 갱신하세요.",
}


class APIError(Exception):
    """키나 쿼리 URL을 포함하지 않는 사용자용 오류."""

    def __init__(self, service: str, code: str, message: str):
        self.service, self.code, self.message = service, str(code), message
        super().__init__(f"{service} [{code}] {message}")


def number(value):
    """누락은 None, 실제 0은 0. 음수 괄호/쉼표를 지원."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("−", "-")
    if text in ("", "-", "--", "N/A", "None", "nan"):
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def ratio(numerator, denominator, scale=1.0):
    n, d = number(numerator), number(denominator)
    return n / d * scale if n is not None and d is not None and d > 0 else None


def fmt(value, decimals=1, suffix=""):
    v = number(value)
    return "—" if v is None else f"{v:,.{decimals}f}{suffix}"


def won(value):
    v = number(value)
    if v is None:
        return "—"
    for divisor, unit in ((1e12, "조 원"), (1e8, "억 원"), (1e4, "만 원")):
        if abs(v) >= divisor:
            return f"{v / divisor:,.2f}{unit}"
    return f"{v:,.0f}원"


def clean_key(name):
    """우선순위: Streamlit secrets(클라우드·secrets.toml) → 환경변수(.env 포함)."""
    try:
        value = str(st.secrets.get(name, "")).strip()
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        value = ""
    return value or os.getenv(name, "").strip()


def check_key(service, key):
    if not key or re.search(r"여기에|YOUR_|PASTE_", key, re.I):
        raise APIError(service, "KEY", "KRX_API_KEY / DART_API_KEY를 .env 또는 Streamlit Secrets에 설정한 뒤 다시 실행하세요.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise APIError(service, "KEY", "인증키에 공백·따옴표·잘못된 문자가 포함되어 있습니다.")
    if service == "OpenDART" and len(key) != 40:
        raise APIError(service, "KEY", "OpenDART 인증키는 40자리입니다.")


def http_get(service, url, *, params=None, headers=None):
    """HTTPS/TLS 검증 유지. 일시적 통신/서버 오류에 한 번만 재시도."""
    for attempt in range(2):
        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=(8, 20),
                allow_redirects=False,
            )
        except requests.exceptions.SSLError:
            raise APIError(service, "TLS", "인증서 검증에 실패했습니다. PC 시간·인증서·보안 프록시를 확인하세요.") from None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == 0:
                time.sleep(0.8)
                continue
            raise APIError(service, "NETWORK", "연결 시간 초과 또는 네트워크 오류입니다. 인터넷·방화벽을 확인하고 다시 시도하세요.") from None
        except requests.exceptions.RequestException:
            raise APIError(service, "NETWORK", "HTTP 요청에 실패했습니다. 네트워크 설정을 확인하세요.") from None
        if response.status_code in (502, 503, 504) and attempt == 0:
            time.sleep(0.8)
            continue
        code = response.status_code
        if code in (401, 403):
            raise APIError(service, str(code), "키 유효기간·API별 이용 승인·허용 IP를 확인하세요. 네트워크 차단도 이 응답을 낼 수 있습니다.")
        if code == 429:
            raise APIError(service, "429", "요청이 제한되었습니다. 잠시 뒤 재시도하고 이용 한도를 확인하세요.")
        if code != 200:
            raise APIError(service, str(code), "서버가 정상 응답하지 않았습니다. 서비스 상태와 공식 URL을 확인하세요.")
        return response
    raise APIError(service, "NETWORK", "연결에 실패했습니다.")


def json_body(response, service):
    try:
        payload = response.json()
    except ValueError:
        raise APIError(service, "FORMAT", "JSON 대신 빈 응답/HTML/XML이 반환되었습니다. 인증·서버 점검·프록시 설정을 확인하세요.") from None
    if not isinstance(payload, dict):
        raise APIError(service, "FORMAT", "예상과 다른 응답 형식입니다.")
    return payload


def parse_krx(payload, day):
    code = str(payload.get("respCode", payload.get("errorCode", "")))
    if code and code not in ("0", "00", "000", "200"):
        raise APIError("KRX", code, "요청이 거절되었습니다. 인증키·API별 승인·이용기간·호출 한도를 확인하세요.")
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise APIError("KRX", "FORMAT", "OutBlock_1 목록이 없습니다. 승인 상태 또는 명세 변경을 확인하세요.")
    if any(not isinstance(r, dict) for r in rows):
        raise APIError("KRX", "FORMAT", "시세 목록 형식이 올바르지 않습니다.")
    if any(str(r.get("BAS_DD", "")).replace("-", "") != day for r in rows):
        raise APIError("KRX", "DATE", "응답 기준일이 요청 날짜와 다릅니다. 샘플 API가 아닌 정식 API인지 확인하세요.")
    return rows


@st.cache_data(ttl=1800, max_entries=800, show_spinner=False)
def krx_rows(api_key, endpoint, day):
    check_key("KRX", api_key)
    if endpoint not in KRX_SERVICES.values():
        raise APIError("KRX", "PATH", "지원되지 않는 API입니다.")
    response = http_get("KRX", f"{KRX_BASE}/{endpoint}",
                        params={"basDd": day}, headers={"AUTH_KEY": api_key})
    return parse_krx(json_body(response, "KRX"), day)


def dart_status(payload):
    code = str(payload.get("status", ""))
    if code == "013":
        return False  # 조회 결과 없음은 통신 오류와 구분
    if code != "000":
        raise APIError("OpenDART", code or "FORMAT", DART_ERRORS.get(code, "응답 상태를 확인할 수 없습니다. 공식 명세를 확인하세요."))
    return True


@st.cache_data(ttl=3600, max_entries=512, show_spinner=False)
def dart_json(api_key, endpoint, **params):
    check_key("OpenDART", api_key)
    response = http_get("OpenDART", f"{DART_BASE}/{endpoint}.json",
                        params={"crtfc_key": api_key, **params})
    payload = json_body(response, "OpenDART")
    if not dart_status(payload):
        return {"list": [], "status": "013"}
    return payload


def parse_corporations(content):
    if not zipfile.is_zipfile(io.BytesIO(content)):
        try:
            root = ET.fromstring(content)
            dart_status({"status": root.findtext("status")})
        except ET.ParseError:
            raise APIError("OpenDART", "ZIP", "고유번호 ZIP 대신 다른 응답이 반환되었습니다.") from None
        raise APIError("OpenDART", "ZIP", "기업코드 ZIP 파일이 없습니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".xml")]
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 150_000_000:
                raise APIError("OpenDART", "ZIP", "기업코드 파일의 크기/구조가 예상과 다릅니다.")
            root = ET.fromstring(archive.read(names[0]))
    except (ET.ParseError, zipfile.BadZipFile, RuntimeError):
        raise APIError("OpenDART", "ZIP", "기업코드 파일을 해석할 수 없습니다.") from None
    records = []
    for node in root.findall("list"):
        stock = (node.findtext("stock_code") or "").strip()
        corp = (node.findtext("corp_code") or "").strip()
        if re.fullmatch(r"[0-9A-Z]{6}", stock) and re.fullmatch(r"\d{8}", corp):
            records.append({"code": stock, "corp": corp,
                            "name": node.findtext("corp_name") or "",
                            "modified": node.findtext("modify_date") or ""})
    if not records:
        raise APIError("OpenDART", "EMPTY", "상장기업 코드가 비어 있습니다.")
    frame = pd.DataFrame(records).sort_values("modified").drop_duplicates("code", keep="last")
    return frame.set_index("code").to_dict("index")


@st.cache_data(ttl=86400, max_entries=4, show_spinner=False)
def corporations(api_key):
    check_key("OpenDART", api_key)
    response = http_get("OpenDART", f"{DART_BASE}/corpCode.xml", params={"crtfc_key": api_key})
    return parse_corporations(response.content)


def default_day(now=None):
    now = now or datetime.now(KST)
    candidate = now.date() - timedelta(days=1 if now.hour >= 8 else 2)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def latest_rows(key, endpoint, requested):
    # 주말·공휴일·미갱신일에는 직전 자료를 탐색. 인증 오류에는 탐색하지 않음.
    for offset in range(16):
        day = requested - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        rows = krx_rows(key, endpoint, day.strftime("%Y%m%d"))
        if rows:
            return day, rows
    raise APIError("KRX", "EMPTY", "최근 16일에 조회 가능한 자료가 없습니다. 승인 기간과 데이터 제공일을 확인하세요.")


STOCK_FIELDS = {
    "ISU_CD": "code", "ISU_NM": "name", "MKT_NM": "market",
    "TDD_CLSPRC": "close", "TDD_OPNPRC": "open", "TDD_HGPRC": "high",
    "TDD_LWPRC": "low", "FLUC_RT": "change", "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value", "MKTCAP": "cap", "LIST_SHRS": "shares",
}


def stock_frame(rows):
    if not rows:
        return pd.DataFrame(columns=STOCK_FIELDS.values())
    frame = pd.DataFrame(rows)
    required = {"ISU_CD", "ISU_NM", "TDD_CLSPRC", "FLUC_RT", "ACC_TRDVOL", "ACC_TRDVAL"}
    if not required.issubset(frame.columns):
        raise APIError("KRX", "SCHEMA", "필수 주식 시세 항목이 누락되었습니다.")
    frame = frame.rename(columns=STOCK_FIELDS).reindex(columns=STOCK_FIELDS.values())
    for col in frame.columns.difference(["code", "name", "market"]):
        frame[col] = frame[col].map(number)
    frame["code"] = frame["code"].astype(str).str.strip()
    return frame.drop_duplicates("code").reset_index(drop=True)


def load_markets(key, requested):
    bundle = {"stocks": [], "indices": {}, "dates": {}, "errors": []}
    for label, endpoint in KRX_SERVICES.items():
        try:
            day, rows = latest_rows(key, endpoint, requested)
            bundle["dates"][label] = day
            market = label.split()[0]
            if label.endswith("주식"):
                data = stock_frame(rows)
                data["market"], data["date"] = market, day
                bundle["stocks"].append(data)
            else:
                aliases = {"KOSPI": {"코스피", "KOSPI"}, "KOSDAQ": {"코스닥", "KOSDAQ"}}
                match = [r for r in rows if str(r.get("IDX_NM", "")).strip().upper() in aliases[market]]
                if not match:
                    raise APIError("KRX", "INDEX", f"{market} 대표 지수를 응답에서 찾지 못했습니다.")
                bundle["indices"][market] = {"close": number(match[0].get("CLSPRC_IDX")),
                                               "change": number(match[0].get("FLUC_RT")), "date": day}
        except APIError as exc:
            bundle["errors"].append(f"{label}: {exc}")
            if exc.code in ("KEY", "NETWORK", "TLS", "429"):
                break
    bundle["stocks"] = pd.concat(bundle["stocks"], ignore_index=True) if bundle["stocks"] else stock_frame([])
    return bundle


def history(key, market, code, end, months, progress=None):
    # 60일 이동평균의 준비 구간을 추가. 52주 범위는 12M 완전 조회 때만 표시.
    start = end - timedelta(days=366 if months == 12 else months * 31 + 95)
    days = list(reversed(pd.bdate_range(start, end).date.tolist()))
    endpoint = KRX_SERVICES[f"{market} 주식"]
    records, error = [], None
    for i, day in enumerate(days):
        try:
            rows = krx_rows(key, endpoint, day.strftime("%Y%m%d"))
            matching = [r for r in rows if str(r.get("ISU_CD", "")).strip() == code]
            if matching:
                frame = stock_frame(matching)
                record = frame.iloc[0].to_dict()
                record["date"] = pd.Timestamp(day)
                records.append(record)
        except APIError as exc:
            error = str(exc)
            break
        if progress:
            progress((i + 1) / len(days), f"주가 이력 {i + 1}/{len(days)}일 확인 · 날짜별 결과 재사용")
    frame = pd.DataFrame(records)
    if not frame.empty:
        frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    full_year = (months == 12 and error is None and not frame.empty
                 and frame.iloc[0]["date"].date() <= end - timedelta(days=355))
    return {"frame": frame, "error": error, "full_year": full_year, "months": months}


def indicators(frame):
    frame = frame.copy()
    close = pd.to_numeric(frame["close"], errors="coerce")
    frame["ma20"] = close.rolling(20, min_periods=20).mean()
    frame["ma60"] = close.rolling(60, min_periods=60).mean()
    # Wilder RSI: 최초 14변동 단순평균 이후 1/14 평활. 중간 누락은 계산 보류.
    values = close.to_numpy(dtype=float)
    rsi = np.full(len(values), np.nan)
    if len(values) >= 15 and np.isfinite(values).all():
        changes = np.diff(values)
        gains, losses = np.maximum(changes, 0), np.maximum(-changes, 0)
        avg_gain, avg_loss = gains[:14].mean(), losses[:14].mean()
        for index in range(14, len(values)):
            if index > 14:
                avg_gain = (avg_gain * 13 + gains[index - 1]) / 14
                avg_loss = (avg_loss * 13 + losses[index - 1]) / 14
            rsi[index] = (50 if avg_gain == 0 else 100) if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    frame["rsi"] = rsi
    frame["volume_ratio"] = frame["volume"] / frame["volume"].shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan)
    return frame


# 표준 XBRL 계정을 우선 사용. 의미가 다른 세부계정의 부분 문자열 매칭은 하지 않음.
ACCOUNTS = {
    "revenue": (["ifrs-full_Revenue", "ifrs_Revenue", "dart_OperatingRevenue"], ["매출액", "매출", "수익(매출액)", "영업수익"], {"IS", "CIS"}),
    "operating": (["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"], ["영업이익", "영업이익(손실)", "영업손익"], {"IS", "CIS"}),
    "profit": (["ifrs-full_ProfitLoss", "ifrs_ProfitLoss"], ["당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"], {"IS", "CIS"}),
    "owner_profit": (["ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs_ProfitLossAttributableToOwnersOfParent"], ["지배기업의소유주에게귀속되는당기순이익(손실)", "지배기업소유주지분순이익"], {"IS", "CIS"}),
    "eps": (["ifrs-full_BasicEarningsLossPerShare", "ifrs_BasicEarningsLossPerShare"], ["기본주당이익", "기본주당이익(손실)", "기본주당순이익", "기본주당순이익(손실)"], {"IS", "CIS"}),
    "equity": (["ifrs-full_Equity", "ifrs_Equity"], ["자본총계"], {"BS"}),
    "owner_equity": (["ifrs-full_EquityAttributableToOwnersOfParent", "ifrs_EquityAttributableToOwnersOfParent"], ["지배기업의소유주에게귀속되는자본", "지배기업소유주지분"], {"BS"}),
    "liabilities": (["ifrs-full_Liabilities", "ifrs_Liabilities"], ["부채총계"], {"BS"}),
    "ocf": (["ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs_CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로부터의현금흐름"], {"CF"}),
}


def compact(text):
    return re.sub(r"\s+", "", str(text or ""))


def account(rows, kind, field="thstrm_amount"):
    ids, names, statements = ACCOUNTS[kind]
    candidates = [r for r in rows if r.get("sj_div") in statements]
    for identifier in ids:
        for row in candidates:
            if row.get("account_id") == identifier:
                value = number(row.get(field))
                if value is not None:
                    return value
    for name in names:
        for row in candidates:
            if compact(row.get("account_nm")) == name:
                value = number(row.get(field))
                if value is not None:
                    return value
    return None


def report_rows(key, corp, year, quarter, basis):
    rows = dart_json(key, "fnlttSinglAcntAll", corp_code=corp, bsns_year=str(year),
                     reprt_code=REPORT_CODES[quarter], fs_div=basis).get("list", [])
    currencies = {r.get("currency", "").strip().upper() for r in rows} - {""}
    if currencies and currencies != {"KRW"}:
        raise APIError("OpenDART", "CURRENCY", "원화가 아닌 재무제표입니다. 환산 없이 주가와 합산하지 않습니다.")
    return rows


def period_number(year, quarter):
    return year * 4 + quarter - 1


def period_parts(index):
    return index // 4, index % 4 + 1


def quarter_amount(reports, index, kind):
    rows = reports.get(index, [])
    if not rows:
        return None
    _, quarter = period_parts(index)
    direct = account(rows, kind)
    if quarter != 4:
        if direct is not None:
            return direct  # DART 명세: 분/반기 IS/CIS thstrm_amount는 3개월 금액
        cumulative = account(rows, kind, "thstrm_add_amount")
        previous = 0 if quarter == 1 else account(reports.get(index - 1, []), kind, "thstrm_add_amount")
        return cumulative - previous if cumulative is not None and previous is not None else None
    previous = account(reports.get(index - 1, []), kind, "thstrm_add_amount")
    return direct - previous if direct is not None and previous is not None else None


def financial_metrics(reports, latest, basis, price, market_cap):
    frame_rows = []
    for index in sorted(reports):
        if not reports[index]:
            continue
        y, q = period_parts(index)
        rec = {"period": f"{y} Q{q}", "index": index}
        rec.update({kind: quarter_amount(reports, index, kind)
                    for kind in ("revenue", "operating", "profit", "owner_profit", "eps")})
        frame_rows.append(rec)
    quarters = pd.DataFrame(frame_rows)
    def ttm(kind):
        values = [quarter_amount(reports, i, kind) for i in range(latest - 3, latest + 1)]
        return sum(values) if all(v is not None for v in values) else None
    current = reports.get(latest, [])
    equity, liability = account(current, "equity"), account(current, "liabilities")
    owner_equity = account(current, "owner_equity") if basis == "CFS" else equity
    old_equity = account(reports.get(latest - 4, []), "owner_equity" if basis == "CFS" else "equity")
    earnings = ttm("owner_profit" if basis == "CFS" else "profit")
    avg_equity = ((owner_equity + old_equity) / 2 if owner_equity is not None and old_equity is not None else None)
    eps = ttm("eps")
    metrics = {
        "per": ratio(price, eps), "pbr": ratio(market_cap, owner_equity),
        "roe": ratio(earnings, avg_equity, 100), "debt": ratio(liability, equity, 100),
        "margin": ratio(ttm("operating"), ttm("revenue"), 100),
        "ocf": account(current, "ocf"), "ttm_eps": eps,
        "revenue_growth": None, "dividend_yield": None,
    }
    rev, old_rev = quarter_amount(reports, latest, "revenue"), quarter_amount(reports, latest - 4, "revenue")
    growth = ratio(rev, old_rev)
    metrics["revenue_growth"] = (growth - 1) * 100 if growth is not None else None
    return quarters, metrics


def extract_dividend(rows):
    matches = [r for r in rows if "주당현금배당금" in compact(r.get("se"))
               and compact(r.get("stock_knd")) in ("보통주", "보통주식")]
    values = [number(r.get("thstrm")) for r in matches]
    values = [v for v in values if v is not None]
    return values[0] if len(set(values)) == 1 else None


def load_financials(key, corp, asof, price, market_cap, progress=None):
    reports, latest, basis = {}, None, None
    # 발행된 최신 분기 탐색. 연결이 없을 때만 동일 분기의 별도를 선택.
    for year in range(asof.year, asof.year - 3, -1):
        for quarter in (4, 3, 2, 1):
            for candidate in ("CFS", "OFS"):
                rows = report_rows(key, corp, year, quarter, candidate)
                if rows:
                    latest, basis = period_number(year, quarter), candidate
                    reports[latest] = rows
                    break
            if latest is not None:
                break
        if latest is not None:
            break
    if latest is None:
        raise APIError("OpenDART", "013", "최근 3개 사업연도에 제공되는 재무제표가 없습니다.")
    # 계산에 필요한 9개 분기. 선택한 연결/별도 기준을 과거에도 그대로 유지.
    for n, index in enumerate(range(latest - 8, latest)):
        year, quarter = period_parts(index)
        reports[index] = report_rows(key, corp, year, quarter, basis)
        if progress:
            progress((n + 1) / 8, "분기 실적과 비교기간을 확인하고 있습니다.")
    quarters, metrics = financial_metrics(reports, latest, basis, price, market_cap)
    year, quarter = period_parts(latest)
    annual_year = year if quarter == 4 else year - 1
    dividends, annual_cash = [], []
    errors = []
    for y in range(annual_year - 2, annual_year + 1):
        idx = period_number(y, 4)
        try:
            if idx not in reports:
                reports[idx] = report_rows(key, corp, y, 4, basis)
            annual_cash.append({"year": str(y), "ocf": account(reports[idx], "ocf")})
            div = dart_json(key, "alotMatter", corp_code=corp, bsns_year=str(y), reprt_code="11011")
            dividends.append({"year": str(y), "dps": extract_dividend(div.get("list", []))})
        except APIError as exc:
            errors.append(str(exc))
            break
    latest_dps = next((r["dps"] for r in dividends if r["year"] == str(annual_year)), None)
    metrics["dividend_yield"] = ratio(latest_dps, price, 100)
    receipt = str(reports[latest][0].get("rcept_no", ""))
    return {"quarters": quarters, "metrics": metrics, "basis": basis,
            "period": f"{year} Q{quarter}", "receipt": receipt,
            "dividends": pd.DataFrame(dividends), "dividend_year": annual_year,
            "annual_cash": pd.DataFrame(annual_cash), "errors": errors}


def disclosures(key, corp, day):
    payload = dart_json(key, "list", corp_code=corp,
                        bgn_de=(day - timedelta(days=180)).strftime("%Y%m%d"),
                        end_de=day.strftime("%Y%m%d"), page_count="100", page_no="1",
                        sort="date", sort_mth="desc", last_reprt_at="N")
    return payload.get("list", [])


def disclosure_tag(title):
    if any(w in title for w in ("소유상황", "대량보유", "주요주주")):
        return "지분"
    if any(w in title for w in ("자기주식", "배당")):
        return "주주환원"
    if any(w in title for w in ("증자", "감자", "전환사채", "신주인수")):
        return "자본변동"
    if any(w in title for w in ("분기보고서", "반기보고서", "사업보고서")):
        return "실적"
    return "공시"


def company_bundle(key, stock, asof, progress=None):
    result = {"financials": None, "disclosures": [], "errors": []}
    try:
        mapping = corporations(key)
        item = mapping.get(stock["code"])
        if not item:
            raise APIError("OpenDART", "MAPPING", "이 종목코드에 대응하는 DART 기업이 없습니다. 우선주·일부 상품의 기업코드는 자동 추정하지 않습니다.")
        corp = item["corp"]
    except APIError as exc:
        result["errors"].append(str(exc))
        return result
    try:
        result["financials"] = load_financials(key, corp, asof, stock.get("close"), stock.get("cap"), progress)
    except APIError as exc:
        result["errors"].append(f"재무정보: {exc}")
    try:
        result["disclosures"] = disclosures(key, corp, asof)
    except APIError as exc:
        result["errors"].append(f"공시: {exc}")
    return result


CSS = """
<style>
.stApp {background:#0b1222; color:#eef3ff;}
[data-testid="stHeader"] {background:#0b1222;}
[data-testid="stSidebar"] {background:#101c30; border-right:1px solid #27364e;}
.block-container {max-width:1780px; padding-top:2rem; padding-bottom:2rem;}
h1 {font-size:2.1rem!important; letter-spacing:-1px; color:#f5f8ff!important;}
h2,h3,h4,p,label {color:inherit;}
[data-testid="stVerticalBlockBorderWrapper"]>div {border-color:#293950!important; border-radius:12px;}
[data-testid="stWidgetLabel"], [data-testid="stCaptionContainer"] {color:#a5b6d0;}
.market-card {border:1px solid #283a53; background:linear-gradient(130deg,#17243b,#111c30);
 border-radius:12px; padding:18px 20px; min-height:140px; margin-bottom:16px;}
.card-name {font-size:13px; color:#a9bdd8; margin-bottom:8px;}
.card-value {font-size:28px; font-weight:750; color:#f2f6ff; line-height:1.2;}
.card-detail {font-size:12px; color:#9eafca; margin-top:9px;}
.up {color:#ff6675!important;} .down {color:#5a9bff!important;}
.tag {display:inline-block; border:1px solid #405774; border-radius:15px; padding:3px 9px;
 font-size:11px; color:#78e7cc; margin:0 7px 4px 0;}
.subtitle {color:#a3b5d1; margin-top:-12px; margin-bottom:20px; font-size:14px;}
.price {font-size:36px; font-weight:750; line-height:1.2;}
.metric-grid {display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:#304059;
 border:1px solid #304059; border-radius:10px; overflow:hidden; margin-bottom:10px;}
.mini {background:#141f34; padding:15px 12px;}
.mini label {font-size:12px; color:#9fb4d3; display:block; margin-bottom:8px;}
.mini strong {font-size:22px; color:#f1f6ff;}
.filing {padding:12px 0; border-bottom:1px solid #24344a;}
.filing a {color:#e6efff; text-decoration:none; font-size:14px;}
.filing small {color:#8da3c3; display:block; padding-top:5px;}
.brand {font-size:24px; font-weight:750; margin:10px 0 18px; color:#cce6ff;}
div.stButton>button[kind="primary"] {background:#65dfc2; color:#0b1928; border:none; font-weight:700;}
</style>
"""


def plot_style(fig, height=270):
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=height,
                      margin=dict(l=8, r=12, t=15, b=15),
                      font=dict(family="Arial, Apple SD Gothic Neo, Malgun Gothic, sans-serif", color=MUTED, size=11),
                      legend=dict(orientation="h", y=1.12, x=0),
                      hoverlabel=dict(bgcolor=PANEL, font_color=TEXT))
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#27344b", zeroline=False)
    return fig


def price_chart(frame, months):
    data = indicators(frame)
    cutoff = data["date"].max() - pd.Timedelta(days=months * 31)
    visible = data[data["date"] >= cutoff]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.78, 0.22])
    # 휴장일은 카테고리 축으로 생략. 지표의 준비 구간은 차트 밖에 유지.
    x = visible["date"].dt.strftime("%Y-%m-%d")
    fig.add_trace(go.Candlestick(x=x, open=visible.open, high=visible.high, low=visible.low,
                                close=visible.close, name="일봉", increasing_line_color=RED,
                                decreasing_line_color=BLUE, showlegend=False), row=1, col=1)
    for col, label, color in (("ma20", "20일선", MINT), ("ma60", "60일선", PURPLE)):
        fig.add_trace(go.Scatter(x=x, y=visible[col], name=label, mode="lines", line=dict(color=color, width=1.8)), row=1, col=1)
    colors = np.where(visible.close >= visible.open, RED, BLUE)
    fig.add_trace(go.Bar(x=x, y=visible.volume, marker_color=colors, name="거래량", showlegend=False), row=2, col=1)
    plot_style(fig, 420)
    fig.update_layout(xaxis_rangeslider_visible=False, hovermode="x unified")
    fig.update_xaxes(type="category", nticks=6)
    fig.update_yaxes(side="right", row=1, col=1)
    return fig, data.iloc[-1]


def market_card(label, value, detail, change=None):
    change = number(change)
    change_html = "" if change is None else f'<span class="{"up" if change >= 0 else "down"}">{change:+.2f}%</span> · '
    st.markdown(f'<div class="market-card"><div class="card-name">{html.escape(label)}</div>'
                f'<div class="card-value">{html.escape(value)}</div><div class="card-detail">'
                f'{change_html}{html.escape(detail)}</div></div>', unsafe_allow_html=True)


def render_market(bundle):
    columns = st.columns(4)
    for i, name in enumerate(("KOSPI", "KOSDAQ")):
        item = bundle["indices"].get(name, {})
        with columns[i]:
            market_card(name, fmt(item.get("close"), 2), str(item.get("date", "조회 전")), item.get("change"))
    stocks = bundle["stocks"]
    valid = not stocks.empty
    scope = " / ".join(sorted(stocks.market.unique())) if valid else "조회 전"
    same_day = valid and stocks["date"].nunique() == 1
    detail = f"{scope} · {stocks['date'].iloc[0]}" if same_day else "시장 기준일 확인 필요"
    with columns[2]:
        total = stocks.value.sum(min_count=1) if same_day else None
        market_card("조회 시장 거래대금", won(total), detail)
    with columns[3]:
        up = int((stocks.change > 0).sum()) if same_day else 0
        down = int((stocks.change < 0).sum()) if same_day else 0
        flat = int((stocks.change == 0).sum()) if same_day else 0
        market_card("상승 / 하락", f"{up:,} / {down:,}" if same_day else "—", f"{detail} · 보합 {flat:,}" if same_day else detail)


def render_core(fin):
    st.subheader("기업 핵심 지표")
    m = fin["metrics"] if fin else {}
    items = [("PER · TTM", fmt(m.get("per"), 1, "배")), ("PBR · 참고값", fmt(m.get("pbr"), 2, "배")),
             ("ROE · TTM", fmt(m.get("roe"), 1, "%")), ("부채비율", fmt(m.get("debt"), 1, "%")),
             ("배당수익률", fmt(m.get("dividend_yield"), 2, "%")), ("영업현금흐름 · 누적", won(m.get("ocf")))]
    st.markdown('<div class="metric-grid">' + "".join(
        f'<div class="mini"><label>{name}</label><strong>{value}</strong></div>' for name, value in items) + '</div>', unsafe_allow_html=True)
    if fin:
        basis = "연결" if fin["basis"] == "CFS" else "별도"
        st.caption(f"{fin['period']} · {basis} · 배당 {fin['dividend_year']}년 기준")
    else:
        st.caption("기업 재무정보를 불러오면 표시됩니다. 누락된 항목은 — 로 표시합니다.")


def render_disclosures(rows, *, expanded=False, demo=False):
    st.subheader("최근 공시")
    if expanded:
        category = st.selectbox("공시 유형", ["전체", "실적", "주주환원", "지분", "자본변동", "공시"])
        rows = [r for r in rows if category == "전체" or disclosure_tag(r.get("report_nm", "")) == category]
    for row in rows[:100 if expanded else 4]:
        title = html.escape(str(row.get("report_nm", "")))
        receipt = str(row.get("rcept_no", ""))
        if re.fullmatch(r"\d{14}", receipt) and not demo:
            title = f'<a href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}" target="_blank" rel="noopener noreferrer">{title} ↗</a>'
        st.markdown(f'<div class="filing"><span class="tag">{disclosure_tag(row.get("report_nm", ""))}</span>{title}'
                    f'<small>{html.escape(str(row.get("rcept_dt", "")))} · OpenDART</small></div>', unsafe_allow_html=True)
    if not rows:
        st.info("조회된 공시가 없습니다. 기업정보 조회 상태와 조회 기간을 확인하세요.")
    st.caption("최근 180일 · 접수일 내림차순 최대 100건 · 정정 보고서 포함")


def render_financial_charts(fin, *, full=False):
    left, right = st.columns([1.1, 1])
    with left, st.container(border=True):
        st.subheader("분기 실적")
        if fin and not fin["quarters"].empty:
            q = fin["quarters"].tail(8 if full else 4)
            fig = go.Figure()
            for col, name, color in (("revenue", "매출", MINT), ("operating", "영업이익", PURPLE)):
                fig.add_bar(x=q.period, y=q[col] / 1e8, name=name, marker_color=color)
            st.plotly_chart(plot_style(fig, 250), width="stretch", key="earnings_chart")
            st.caption("단위: 억 원 · 개별 분기 실적 · Q4 = 연간 − 3분기 누적")
            st.markdown(f"매출 YoY **{fmt(fin['metrics'].get('revenue_growth'), 1, '%')}**")
        else:
            st.info("기업정보를 조회하면 분기 실적이 표시됩니다.")
    with right, st.container(border=True):
        st.subheader("배당 · 재무 건전성")
        if fin:
            div = fin["dividends"]
            if not div.empty and div.dps.notna().any():
                fig = go.Figure(go.Bar(x=div.year, y=div.dps, marker_color=PURPLE,
                                      text=div.dps, texttemplate="%{y:,.0f}", textposition="outside"))
                fig.update_xaxes(type="category")
                st.plotly_chart(plot_style(fig, 210), width="stretch", key="dividend_chart")
                st.caption("주당현금배당금 · 보통주 · 원 · 누락 연도는 빈칸")
            else:
                st.info("비교 가능한 보통주 배당 자료가 없습니다.")
            st.markdown(f"영업이익률 · TTM **{fmt(fin['metrics'].get('margin'), 1, '%')}**")
            cash = fin["annual_cash"]
            valid = not cash.empty and len(cash) == 3 and cash.ocf.notna().all()
            message = "3년 연속 양수" if valid and (cash.ocf > 0).all() else "3년 중 음수 또는 0 포함" if valid else "3개년 자료 부족"
            st.caption(f"연간 영업현금흐름: {message}")
        else:
            st.info("기업정보를 조회하면 배당과 현금흐름이 표시됩니다.")


def explain_metrics():
    with st.expander("계산 기준과 데이터 제공 범위"):
        st.markdown("""
- **PER** = 시세 기준일 종가 ÷ 최근 4개 분기 공시 기본 EPS 합계. 적자/0/분기 누락은 표시하지 않습니다.
- **PBR 참고값** = 선택 종목 시가총액 ÷ 지배주주 자본(별도는 자본총계). 우선주·자기주식 처리 차이가 있어 공식 PBR과 다를 수 있습니다.
- **ROE** = 최근 4개 분기 지배주주 순이익 ÷ 최근·전년 동기 지배주주 자본 평균. 별도는 순이익·자본총계로 계산합니다.
- **배당수익률** = 표시된 최근 결산연도 보통주 주당현금배당금 ÷ 시세 기준일 종가. 미래 예상 배당이 아닙니다.
- **영업현금흐름**은 표시된 재무기간의 누적 금액입니다. 분기 그래프는 누적이 아닌 개별 분기입니다.
- **주가**는 KRX 단순주가입니다. 액면분할·병합·권리락은 이동평균·RSI·52주 범위에 영향을 줍니다. 52주 범위는 12M 이력을 온전히 불러온 경우만 표시합니다.
- 재무제표는 현재 조회 가능한 정정 결과를 반영합니다. 과거 시점의 정보만을 재현하는 백테스트용 데이터가 아닙니다.
- 금융업 등 표준 매출·영업이익 계정이 없는 기업, 우선주, 일부 상품은 재무 항목이 비어 있을 수 있습니다.
""")


def run_diagnostics(krx_key, dart_key, requested):
    st.subheader("API 연결 진단")
    st.write("입력한 키로 각 서비스를 확인합니다. 정상 응답이 온 항목만 ‘정상’으로 표시합니다.")
    st.caption("KRX: API별 승인 / 영업일 익일 08:00 갱신 / 샘플 페이지에는 개인 키 사용 불가")
    diagnostic_token = (hashlib.sha256(f"{krx_key}|{dart_key}".encode()).hexdigest(), str(requested))
    if st.button("연결 진단 실행", type="primary", key="diagnose"):
        # 진단 버튼은 캐시된 성공 응답이 아닌 현재 인증 상태를 점검.
        krx_rows.clear()
        dart_json.clear()
        corporations.clear()
        results = []
        placeholder = st.empty()
        for label, endpoint in KRX_SERVICES.items():
            try:
                day, rows = latest_rows(krx_key, endpoint, requested)
                results.append({"서비스": label, "결과": "정상", "설명": f"{day} · {len(rows):,}건"})
            except APIError as exc:
                results.append({"서비스": label, "결과": "확인 필요", "설명": str(exc)})
            placeholder.dataframe(pd.DataFrame(results), hide_index=True, width="stretch")
        try:
            mapping = corporations(dart_key)
            results.append({"서비스": "OpenDART 기업코드", "결과": "정상", "설명": f"상장 종목 {len(mapping):,}개 매핑"})
            item = mapping.get("005930") or next(iter(mapping.values()))
            payload = dart_json(dart_key, "list", corp_code=item["corp"], page_count="1",
                                bgn_de=(datetime.now(KST).date() - timedelta(days=180)).strftime("%Y%m%d"))
            results.append({"서비스": "OpenDART 공시검색", "결과": "정상" if payload.get("list") else "자료 없음",
                            "설명": "인증된 조회 응답 확인"})
            fin = load_financials(dart_key, item["corp"], datetime.now(KST).date(), None, None)
            results.append({"서비스": "OpenDART 재무제표·배당", "결과": "정상" if not fin["errors"] else "일부 확인 필요",
                            "설명": f"{fin['period']} {fin['basis']} · " + (" / ".join(fin["errors"]) or "재무 응답 확인; 개별 계정·배당 누락 가능")})
        except APIError as exc:
            results.append({"서비스": "OpenDART", "결과": "확인 필요", "설명": str(exc)})
        st.session_state["diagnostics"] = results
        st.session_state["diagnostic_token"] = diagnostic_token
        st.session_state["diagnostic_time"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        placeholder.empty()
    if "diagnostics" in st.session_state and st.session_state.get("diagnostic_token") == diagnostic_token:
        st.caption(f"진단 시각: {st.session_state['diagnostic_time']}")
        st.dataframe(pd.DataFrame(st.session_state["diagnostics"]), hide_index=True, width="stretch")
    st.markdown("[KRX API 승인·이용방법](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO003.jsp) · "
                "[KRX FAQ](https://openapi.krx.co.kr/contents/OPP/COMM/faq/OPPCOMM004.cmd) · "
                "[OpenDART 개발가이드](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001)")


def demo_data(day):
    """명시적으로 선택한 디자인 미리보기에서만 사용하는 재현 가능한 가상 데이터."""
    rng = np.random.default_rng(23)
    dates = pd.bdate_range(end=day, periods=290)
    close = np.exp(np.cumsum(rng.normal(0.001, 0.013, len(dates))))
    close = np.round(close / close[-1] * 72400 / 100) * 100
    op = close * (1 + rng.normal(0, 0.006, len(dates)))
    hist = pd.DataFrame({"date": dates, "close": close, "open": op,
                         "high": np.maximum(close, op) * 1.012,
                         "low": np.minimum(close, op) * 0.988,
                         "volume": rng.integers(3_000_000, 20_000_000, len(dates))})
    stocks = pd.DataFrame([
        dict(code="DEMO01", name="가상테크", market="KOSPI", close=72400, change=1.26, value=6.2e11, volume=8500000, cap=14.48e12, date=day),
        dict(code="DEMO02", name="한빛산업", market="KOSPI", close=38100, change=2.14, value=3.2e11, volume=8400000, cap=7.6e12, date=day),
        dict(code="DEMO03", name="푸른소재", market="KOSDAQ", close=21500, change=-0.82, value=1.8e11, volume=8300000, cap=4.3e12, date=day),
    ])
    bundle = {"stocks": stocks, "indices": {"KOSPI": dict(close=2850.42, change=0.84, date=day),
                                             "KOSDAQ": dict(close=912.36, change=-0.32, date=day)}, "errors": [], "dates": {}}
    q = pd.DataFrame({"period": ["2025 Q3", "2025 Q4", "2026 Q1", "2026 Q2"],
                      "revenue": np.array([1.1, 1.3, 1.5, 1.8]) * 1e12,
                      "operating": np.array([.14, .18, .20, .26]) * 1e12})
    fin = {"quarters": q, "metrics": dict(per=12.8, pbr=1.15, roe=9.6, debt=38.2, dividend_yield=2.4, ocf=1.2e12, margin=14.2, revenue_growth=12.4),
           "period": "2026 Q2", "basis": "CFS", "receipt": "", "dividend_year": 2025,
           "dividends": pd.DataFrame({"year": ["2023", "2024", "2025"], "dps": [700, 1000, 1200]}),
           "annual_cash": pd.DataFrame({"year": ["2023", "2024", "2025"], "ocf": [7e11, 9e11, 1.2e12]}), "errors": []}
    filings = [{"report_nm": title + " (예시)", "rcept_dt": "가상 공시", "rcept_no": ""}
               for title in ("반기보고서", "자기주식 취득 결정", "주요주주 소유상황보고서")]
    return bundle, {"frame": hist, "error": None, "full_year": True, "months": 6}, {"financials": fin, "disclosures": filings, "errors": []}


def render_watchlist(stocks, dart_key, today, signature, demo):
    with st.container(border=True):
        st.subheader("관심종목 스크리너")
        options = stocks.code.tolist()
        names = dict(zip(stocks.code, stocks.name))
        defaults = stocks.sort_values("cap", ascending=False, na_position="last").code.tolist()[:3]
        selected = st.multiselect("관심종목 · 최대 5개", options, default=defaults,
                                  format_func=lambda c: f"{names[c]} · {c}", max_selections=5, key="watchlist")
        if st.button("관심종목 재무정보 조회", disabled=demo or not selected, key="watchload"):
            progress = st.progress(0.0)
            for i, code in enumerate(selected):
                stock = stocks[stocks.code == code].iloc[0].to_dict()
                cache_id = (signature, code, str(stock["date"]))
                data = company_bundle(dart_key, stock, today)
                st.session_state.setdefault("companies", {})[cache_id] = data
                progress.progress((i + 1) / len(selected), text=f"{i + 1}/{len(selected)}개 조회 완료")
            progress.empty()
        rows, failures = [], []
        for i, code in enumerate(selected):
            stock = stocks[stocks.code == code].iloc[0]
            cache_id = (signature, code, str(stock["date"]))
            result = st.session_state.get("companies", {}).get(cache_id, {})
            fin = result.get("financials")
            metrics = fin["metrics"] if fin else {}
            if demo:
                metrics = dict(per=[12.8, 10.4, 16.2][i % 3], roe=[9.6, 14.1, 11.8][i % 3], revenue_growth=[12.4, 16.1, 8.3][i % 3])
            if result.get("errors"):
                failures.append(f"{stock['name']}: {' / '.join(result['errors'])}")
            rows.append({"종목": stock["name"], "종가": stock["close"], "등락률(%)": stock["change"],
                         "PER": metrics.get("per"), "ROE(%)": metrics.get("roe"),
                         "매출 YoY(%)": metrics.get("revenue_growth"),
                         "재무기간": fin["period"] if fin else "예시" if demo else "미조회/자료 없음"})
        col1, col2 = st.columns(2)
        roe_filter = col1.checkbox("ROE > 10%", key="roe_filter")
        growth_filter = col2.checkbox("매출 YoY > 10%", key="growth_filter")
        table = pd.DataFrame(rows)
        if not table.empty:
            if roe_filter:
                table = table[pd.to_numeric(table["ROE(%)"], errors="coerce") > 10]
            if growth_filter:
                table = table[pd.to_numeric(table["매출 YoY(%)"], errors="coerce") > 10]
            st.dataframe(table, hide_index=True, width="stretch",
                         column_config={"종가": st.column_config.NumberColumn(format="%,d"),
                                        "등락률(%)": st.column_config.NumberColumn(format="%+.2f"),
                                        "PER": st.column_config.NumberColumn(format="%.1f"),
                                        "ROE(%)": st.column_config.NumberColumn(format="%.1f"),
                                        "매출 YoY(%)": st.column_config.NumberColumn(format="%.1f")})
            st.download_button("비교표 CSV", table.to_csv(index=False).encode("utf-8-sig"), "watchlist.csv", "text/csv")
        st.caption("관심종목 범위에서만 조건 검색합니다. 미조회·누락 값은 조건 충족으로 처리하지 않습니다.")
        if failures:
            with st.expander("관심종목 조회 안내"):
                for failure in failures:
                    st.warning(failure)


def main():
    st.set_page_config(page_title=TITLE, page_icon="📊", layout="wide", initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    krx_key, dart_key = clean_key("KRX_API_KEY"), clean_key("DART_API_KEY")
    today = datetime.now(KST).date()
    signature = hashlib.sha256(f"{krx_key}|{dart_key}".encode()).hexdigest()
    with st.sidebar:
        st.markdown('<div class="brand">▥ 주식 분석</div>', unsafe_allow_html=True)
        mode = st.radio("데이터 모드", ["실제 데이터", "디자인 미리보기"], key="mode")
        page = st.radio("화면", ["대시보드", "재무 상세", "공시", "연결 진단"], key="page")
        requested = st.date_input("시세 조회 기준일", value=default_day(),
                                  min_value=date(2015, 1, 1), max_value=today - timedelta(days=1))
        months = st.select_slider("차트 기간", options=[1, 3, 6, 12], value=6,
                                  format_func=lambda m: f"{m}M", key="months")
        st.caption("KRX는 영업일 익일 오전 8시 갱신. 자료가 없으면 직전 제공일을 찾습니다.")
        refresh = st.button("시장 데이터 새로고침", width="stretch")
        if st.button("조회 캐시 초기화", width="stretch"):
            krx_rows.clear()
            dart_json.clear()
            corporations.clear()
            for name in ("market_bundle", "market_token", "companies", "histories", "diagnostics"):
                st.session_state.pop(name, None)
            st.rerun()
        st.markdown("---")
        st.caption("KRX × OpenDART\n\n가격 · 실적 · 공시를 한눈에")
    st.title(TITLE)
    st.markdown('<div class="subtitle">가격 · 실적 · 공시를 한눈에 &nbsp; | &nbsp; KRX × OpenDART</div>', unsafe_allow_html=True)
    if page == "연결 진단":
        run_diagnostics(krx_key, dart_key, requested)
        return
    demo = mode == "디자인 미리보기"
    if demo:
        st.warning("디자인 미리보기 · 모든 종목·수치·공시는 가상 예시입니다. 실제 API를 조회하지 않습니다.")
        bundle, hist, company = demo_data(requested)
    else:
        token = (signature, str(requested))
        if not krx_key:
            st.info("API 키가 설정되지 않았습니다. 로컬에서는 .env 파일, Streamlit Cloud에서는 Settings → Secrets에 아래 두 줄을 넣으세요. 설정 후 ‘연결 진단’에서 결과를 확인할 수 있습니다.")
            st.code('KRX_API_KEY = "여기에_KRX_키"\nDART_API_KEY = "여기에_OpenDART_키"', language="toml")
            st.caption("화면 디자인은 왼쪽 ‘디자인 미리보기’에서 확인할 수 있습니다.")
            return
        if refresh or st.session_state.get("market_token") != token:
            if refresh:
                krx_rows.clear()
            with st.spinner("KRX 주식·지수의 최근 제공일을 확인하고 있습니다…"):
                st.session_state["market_bundle"] = load_markets(krx_key, requested)
            st.session_state["market_token"] = token
        bundle = st.session_state["market_bundle"]
    render_market(bundle)
    for error in bundle["errors"]:
        st.warning(error)
    stocks = bundle["stocks"]
    if stocks.empty:
        st.info("주식 데이터를 불러오지 못했습니다. ‘연결 진단’에서 KRX 승인과 인증 상태를 확인하세요.")
        return
    names = dict(zip(stocks.code, stocks.name))
    options = stocks.sort_values("cap", ascending=False, na_position="last").code.tolist()
    preferred = "005930" if "005930" in options else options[0]
    code = st.selectbox("종목명 또는 코드 검색", options, index=options.index(preferred),
                        format_func=lambda c: f"{names[c]} · {c}", key="stock_picker")
    stock = stocks[stocks.code == code].iloc[0].to_dict()
    cache_id = (signature, code, str(stock["date"]))
    if not demo:
        company = st.session_state.get("companies", {}).get(cache_id, {"financials": None, "disclosures": [], "errors": []})
        hist = st.session_state.get("histories", {}).get((*cache_id, months))
        a, b = st.columns([1, 3])
        with a:
            load = st.button("선택 종목 분석 불러오기", type="primary", width="stretch")
        with b:
            st.caption(f"최초 이력 조회는 약 {len(pd.bdate_range(stock['date'] - timedelta(days=366 if months == 12 else months * 31 + 95), stock['date']))}개 날짜를 확인해 시간이 걸립니다. 이후 같은 날짜의 자료는 캐시를 사용합니다.")
        if load:
            progress = st.progress(0.0, text="조회 준비")
            hist = history(krx_key, stock["market"], code, stock["date"], months, progress.progress)
            st.session_state.setdefault("histories", {})[(*cache_id, months)] = hist
            progress.progress(0.0, text="OpenDART 기업·재무정보를 확인하고 있습니다.")
            company = company_bundle(dart_key, stock, today, progress.progress)
            st.session_state.setdefault("companies", {})[cache_id] = company
            progress.empty()
    fin = company.get("financials")
    for error in company.get("errors", []) + (fin.get("errors", []) if fin else []):
        st.warning(error)
    if page == "공시":
        render_disclosures(company["disclosures"], expanded=True, demo=demo)
        return
    if page == "재무 상세":
        render_core(fin)
        render_financial_charts(fin, full=True)
        if fin:
            st.dataframe(fin["quarters"].drop(columns="index", errors="ignore"), hide_index=True, width="stretch")
            if re.fullmatch(r"\d{14}", fin.get("receipt", "")):
                st.link_button("재무제표 공시 원문", f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={fin['receipt']}")
        explain_metrics()
        return
    left, right = st.columns([2.1, 1], gap="medium")
    with left, st.container(border=True):
        st.subheader(f"{stock['name']}  ·  {code}")
        change = number(stock.get("change"))
        delta = "—" if change is None else f"{change:+.2f}%"
        cls = "up" if change is not None and change >= 0 else "down"
        st.markdown(f'<div class="price">{fmt(stock.get("close"), 0, "원")} &nbsp; <span class="{cls}" style="font-size:23px">{delta}</span></div>', unsafe_allow_html=True)
        st.caption(f"{stock['market']} · 시세 기준일 {stock['date']} · 시가총액 {won(stock.get('cap'))}")
        if hist and not hist["frame"].empty:
            # 데모 종목을 바꾸어도 마지막 종가가 선택 종목과 맞도록 스케일 조정.
            frame = hist["frame"].copy()
            if demo:
                scale = stock["close"] / frame.close.iloc[-1]
                frame[["close", "open", "high", "low"]] *= scale
            chart, latest = price_chart(frame, months)
            st.plotly_chart(chart, width="stretch", key="price_chart")
            one, two, three = st.columns(3)
            one.metric("RSI · 14일", fmt(latest["rsi"], 1))
            two.metric("직전 20일 거래량 대비", fmt(latest["volume_ratio"], 2, "배"))
            year_frame = frame[frame.date >= frame.date.max() - pd.Timedelta(days=364)]
            three.metric("52주 저가 / 고가", f"{fmt(year_frame.low.min(), 0)} / {fmt(year_frame.high.max(), 0)}" if hist["full_year"] and months == 12 else "12M 조회 필요")
            if hist["error"]:
                st.warning(f"이력 조회가 중단되어 일부 구간만 표시합니다. {hist['error']}")
            st.caption("KRX 단순주가 · 이동평균/RSI 자체 계산 · 액면분할 등 미조정")
        else:
            st.info("‘선택 종목 분석 불러오기’를 누르면 일봉·거래량·이동평균을 표시합니다.")
            if hist and hist["error"]:
                st.warning(hist["error"])
    with right:
        with st.container(border=True):
            render_core(fin)
        with st.container(border=True):
            render_disclosures(company["disclosures"], demo=demo)
    bottom_left, bottom_right = st.columns([2.1, 1], gap="medium")
    with bottom_left:
        render_financial_charts(fin)
    with bottom_right:
        render_watchlist(stocks, dart_key, today, signature, demo)
    explain_metrics()
    st.caption("시세 기준일과 재무기간을 구분해 표시합니다. — 는 조회 전 또는 계산에 필요한 자료 부족을 뜻합니다.")


if __name__ == "__main__":
    main()
