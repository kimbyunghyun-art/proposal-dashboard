# Construction Equipment Weekly
## 건설기계 주간 뉴스레터 자동화 시스템

NYT 1면 스타일의 건설기계 분야 주간 뉴스레터를 자동으로 생성하고 이메일로 발송하는 시스템입니다.

---

## 파일 구조

```
proposal-dashboard/
├── newsletter.html          # NYT 스타일 뉴스레터 템플릿 (브라우저에서 미리보기 가능)
├── newsletter_generator.py  # 뉴스 수집 → 검증 → HTML 생성 → 이메일 발송 스크립트
├── config.json              # RSS 피드, SMTP, 수신자 목록, 검증 설정
├── requirements.txt         # Python 의존성
└── generated/               # 생성된 뉴스레터 HTML 파일 저장 (자동 생성)
```

---

## 빠른 시작

### 1. 의존성 설치
```bash
pip install -r requirements.txt
```

### 2. `config.json` 설정
```json
{
  "email": {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "your-email@gmail.com",
    "smtp_password": "YOUR_APP_PASSWORD"
  }
}
```

> **Gmail 앱 비밀번호**: Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호 생성

### 3. 수신자 추가
```bash
# 직접 config.json 수정 또는:
python newsletter_generator.py --add-recipient "name@company.com" "홍길동"
```

### 4. 미리보기 생성 (이메일 발송 없이)
```bash
python newsletter_generator.py --preview
# → generated/newsletter_YYYY-MM-DD.html 생성
```

### 5. 뉴스레터 생성 및 발송
```bash
python newsletter_generator.py
```

---

## 자동 발송 설정 (crontab — 매주 월요일 오전 8시)

```bash
crontab -e
```
```cron
0 8 * * 1 /path/to/venv/bin/python /path/to/newsletter_generator.py >> /var/log/newsletter.log 2>&1
```

---

## 소스 검증 방법론 (6단계)

| 단계 | 방법 | 설명 |
|------|------|------|
| 1 | **도메인 화이트리스트** | 사전 승인된 도메인만 수집 대상 포함 |
| 2 | **HTTPS 강제** | HTTP 비암호화 소스 자동 제외 |
| 3 | **발행 날짜 확인** | 날짜 없는 기사 점수 감점 처리 |
| 4 | **크로스 소스 검증** | Top Story는 2개 이상 검증 소스 보도 필요 |
| 5 | **협회 공인 확인** | AEM·CECE·KOCEMA 인증 매체 우선 가중치 부여 |
| 6 | **키워드 관련성** | 건설기계 핵심 키워드 매칭 스코어 계산 |

### 검증된 소스 목록

**영어 (English)**
- Equipment World (equipmentworld.com) — Randall-Reilly, AEM 협력, 50년+
- Construction Equipment Magazine — Access Intelligence, CECE 협력
- Engineering News-Record / ENR (enr.com) — BNP Media, 100년+
- International Construction / KHL (khl.com) — AEM/CECE 공인
- Reuters / Bloomberg — 글로벌 1차 금융·산업 뉴스

**한국어 (Korean)**
- 건설기계신문 / CEMnews (cemnews.co.kr) — 40년+ 전문 일간지
- 한국건설기계산업협회 / KOCEMA (kocema.or.kr) — 공식 협회, 1차 통계
- DART 전자공시 (dart.fss.or.kr) — FSS 운영, 상장사 공식 공시
- 현대건설기계·두산밥캣·HD현대인프라코어 IR — 기업 공식 정보

---

## 이메일 서비스 옵션

| 서비스 | SMTP 호스트 | 포트 | 비고 |
|--------|-------------|------|------|
| Gmail | smtp.gmail.com | 587 | 앱 비밀번호 필요 |
| Naver | smtp.naver.com | 587 | 네이버 SMTP 활성화 필요 |
| SendGrid | smtp.sendgrid.net | 587 | 대량 발송에 권장 |
| AWS SES | email-smtp.ap-northeast-2.amazonaws.com | 587 | 엔터프라이즈 권장 |

---

## 뉴스레터 디자인

- **NYT 1면 레이아웃**: 3단 그리드 (한국 / Top Story / 글로벌)
- **Breaking Ticker**: 주요 뉴스 자동 스크롤
- **Market Snapshot**: 주요 건설기계 OEM 주가 현황
- **Source Verification Box**: 검증 소스 목록 및 방법론 상시 표시
- **구독 모달**: 신규 수신자 자동 등록
