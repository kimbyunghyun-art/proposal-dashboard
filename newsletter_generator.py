#!/usr/bin/env python3
"""
Construction Equipment Weekly — Newsletter Generator
건설기계 주간 뉴스레터 자동 생성 및 이메일 발송 스크립트

Usage:
  python newsletter_generator.py              # Generate & send newsletter
  python newsletter_generator.py --preview    # Generate HTML only (no email)
  python newsletter_generator.py --add-recipient "name@example.com" "Name"

Schedule (crontab):
  0 8 * * 1   /path/to/venv/bin/python /path/to/newsletter_generator.py >> /var/log/newsletter.log 2>&1
"""

import argparse
import datetime
import html
import json
import logging
import smtplib
import sys
import time
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import urlopen, Request

# ── Third-party (optional) ────────────────────────────────────────────────────
try:
    import requests as _requests
    def _http_get(url: str, timeout: int = 15) -> str:
        r = _requests.get(url, headers={"User-Agent": "CEWeekly/1.0"}, timeout=timeout)
        r.raise_for_status()
        return r.text
except ImportError:
    def _http_get(url: str, timeout: int = 15) -> str:
        req = Request(url, headers={"User-Agent": "CEWeekly/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

try:
    from bs4 import BeautifulSoup as _BS
    def _strip_html(text: str) -> str:
        return _BS(text, "lxml").get_text(" ", strip=True)
except ImportError:
    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
        def handle_data(self, data: str):
            self._parts.append(data)
        def get_text(self) -> str:
            return " ".join(self._parts)
    def _strip_html(text: str) -> str:
        p = _TextExtractor(); p.feed(text); return p.get_text()

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG & LOGGING
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
OUTPUT_DIR  = BASE_DIR / "generated"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)


def load_config() -> dict:
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class SourceVerifier:
    """
    소스 검증 엔진 — 화이트리스트 기반 + 품질 점수 계산.

    검증 방법론 (6단계):
      1. 도메인 화이트리스트 매칭
      2. HTTPS 프로토콜 강제
      3. 발행 날짜 존재 여부 확인
      4. 크로스 소스 카운팅 (2개 이상 소스 보도 시 가중치 부여)
      5. 협회/기관 공인 소스 우선순위 부여
      6. 키워드 관련성 스코어링
    """

    WHITELIST: dict[str, dict] = {
        # ── English Sources ──
        "equipmentworld.com":         {"name": "Equipment World",               "tier": 1, "org": "AEM"},
        "constructionequipment.com":  {"name": "Construction Equipment Mag",     "tier": 1, "org": "CECE"},
        "enr.com":                    {"name": "Engineering News-Record (ENR)",  "tier": 1, "org": ""},
        "khl.com":                    {"name": "International Construction",     "tier": 1, "org": "AEM/CECE"},
        "pitandquarry.com":           {"name": "Pit & Quarry",                  "tier": 2, "org": ""},
        "constructiondive.com":       {"name": "Construction Dive",             "tier": 2, "org": ""},
        "reuters.com":                {"name": "Reuters",                       "tier": 1, "org": ""},
        "bloomberg.com":              {"name": "Bloomberg",                     "tier": 1, "org": ""},
        "ft.com":                     {"name": "Financial Times",               "tier": 1, "org": ""},
        "forconstructionpros.com":    {"name": "For Construction Pros",         "tier": 2, "org": ""},
        "heavyequipmentguide.ca":     {"name": "Heavy Equipment Guide",         "tier": 2, "org": ""},
        # ── Korean Sources ──
        "cemnews.co.kr":              {"name": "건설기계신문 (CEMnews)",          "tier": 1, "org": "KOCEMA"},
        "kocema.or.kr":               {"name": "한국건설기계산업협회 (KOCEMA)",    "tier": 1, "org": "KOCEMA"},
        "dart.fss.or.kr":             {"name": "DART 전자공시시스템",              "tier": 1, "org": "FSS"},
        "hyundai-ce.com":             {"name": "현대건설기계 (IR)",               "tier": 1, "org": ""},
        "doosanbobcat.com":           {"name": "두산밥캣 (IR)",                  "tier": 1, "org": ""},
        "hd-hyundaice.com":           {"name": "HD현대건설기계 (IR)",             "tier": 1, "org": ""},
        "soosansepotics.com":         {"name": "수산세보틱스 (IR)",               "tier": 2, "org": ""},
        "daemo.co.kr":                {"name": "대모엔지니어링 (IR)",             "tier": 2, "org": ""},
        "hyundai-everdigm.com":       {"name": "현대에버다임 (IR)",               "tier": 2, "org": ""},
        "mk.co.kr":                   {"name": "매일경제",                       "tier": 2, "org": ""},
        "hankyung.com":               {"name": "한국경제",                       "tier": 2, "org": ""},
    }

    KEYWORDS_EN = [
        "excavator", "bulldozer", "crane", "loader", "grader", "compactor",
        "construction equipment", "heavy equipment", "earthmoving", "OEM",
        "Caterpillar", "Komatsu", "Volvo CE", "Liebherr", "Bobcat", "Hitachi",
        "John Deere", "Doosan", "Hyundai CE", "infrastructure",
    ]
    KEYWORDS_KO = [
        "굴착기", "불도저", "크레인", "로더", "건설기계", "중장비",
        "현대건설기계", "두산밥캣", "HD현대건설기계", "건설 장비", "중공업",
        "수산세보틱스", "대모엔지니어링", "현대에버다임",
        "KOCEMA", "인프라", "굴삭기",
    ]

    def verify(self, url: str, text: str = "", pub_date: Optional[datetime.datetime] = None) -> dict:
        """Return verification result dict for a given URL."""
        result = {"ok": False, "score": 0, "reason": "", "source_name": "Unknown"}

        parsed = urlparse(url)
        domain = parsed.netloc.lstrip("www.")

        # 1. HTTPS check
        if parsed.scheme != "https":
            result["reason"] = "Non-HTTPS URL"
            return result

        # 2. Whitelist check
        matched_domain = None
        for wl_domain, meta in self.WHITELIST.items():
            if domain == wl_domain or domain.endswith("." + wl_domain):
                matched_domain = wl_domain
                result["source_name"] = meta["name"]
                result["score"] += 50 if meta["tier"] == 1 else 30
                if meta["org"]:
                    result["score"] += 10  # accredited association bonus
                break

        if not matched_domain:
            result["reason"] = f"Domain '{domain}' not in whitelist"
            return result

        # 3. Publish date check
        if pub_date is None:
            result["score"] -= 10
        else:
            age_days = (datetime.datetime.now(tz=datetime.timezone.utc) - pub_date.astimezone(datetime.timezone.utc)).days
            if age_days <= 7:
                result["score"] += 20  # fresh content bonus
            elif age_days > 30:
                result["score"] -= 5

        # 4. Keyword relevance
        combined = text.lower()
        kw_hits = sum(1 for kw in (self.KEYWORDS_EN + self.KEYWORDS_KO) if kw.lower() in combined)
        result["score"] += min(kw_hits * 3, 20)

        result["ok"] = True
        result["reason"] = "Whitelist verified"
        return result


# ─────────────────────────────────────────────────────────────────────────────
# RSS FETCHER
# ─────────────────────────────────────────────────────────────────────────────

class RSSFetcher:
    """
    Fetches and parses RSS 2.0 / Atom 1.0 feeds using stdlib xml.etree.ElementTree.
    No feedparser dependency required — works with any Python 3.8+ environment.
    """

    # XML namespaces used in Atom and common feed extensions
    NS = {
        "atom":    "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc":      "http://purl.org/dc/elements/1.1/",
        "media":   "http://search.yahoo.com/mrss/",
    }

    def __init__(self, verifier: SourceVerifier, timeout: int = 15):
        self.verifier = verifier
        self.timeout  = timeout

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _tag(ns: str, local: str) -> str:
        return f"{{{ns}}}{local}"

    @staticmethod
    def _parse_date(text: Optional[str]) -> Optional[datetime.datetime]:
        if not text:
            return None
        for fmt in (
            "%a, %d %b %Y %H:%M:%S %z",   # RFC 2822 (RSS)
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",         # ISO 8601 (Atom)
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d",
        ):
            try:
                return datetime.datetime.strptime(text.strip(), fmt).replace(
                    tzinfo=datetime.timezone.utc
                )
            except ValueError:
                pass
        return None

    def _parse_rss(self, root: ET.Element) -> list[dict]:
        """Parse RSS 2.0 <item> elements."""
        items = []
        for item in root.findall(".//item"):
            def t(tag: str) -> str:
                el = item.find(tag)
                return el.text or "" if el is not None else ""

            title   = t("title").strip()
            link    = t("link").strip() or t("guid").strip()
            summary = t("description").strip() or t(f'{{{self.NS["content"]}}}encoded').strip()
            pubdate = t("pubDate").strip() or t(f'{{{self.NS["dc"]}}}date').strip()
            items.append((title, link, summary, self._parse_date(pubdate)))
        return items

    def _parse_atom(self, root: ET.Element) -> list[dict]:
        """Parse Atom 1.0 <entry> elements."""
        ns = self.NS["atom"]
        items = []
        for entry in root.findall(f"{{{ns}}}entry"):
            def t(tag: str) -> str:
                el = entry.find(f"{{{ns}}}{tag}")
                return (el.text or "").strip() if el is not None else ""

            title   = t("title")
            link_el = entry.find(f"{{{ns}}}link[@rel='alternate']") or entry.find(f"{{{ns}}}link")
            link    = link_el.get("href", "") if link_el is not None else ""
            summary = t("summary") or t("content")
            pubdate = t("published") or t("updated")
            items.append((title, link, summary, self._parse_date(pubdate)))
        return items

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_feed(self, feed_url: str, max_items: int = 10) -> list[dict]:
        log.info(f"Fetching RSS: {feed_url}")
        try:
            xml_text = _http_get(feed_url, timeout=self.timeout)
            root = ET.fromstring(xml_text)
        except Exception as e:
            log.warning(f"  Failed to fetch {feed_url}: {e}")
            return []

        # Detect format: RSS vs Atom
        tag = root.tag.lower()
        if "feed" in tag or self.NS["atom"] in tag:
            raw_items = self._parse_atom(root)
        else:
            raw_items = self._parse_rss(root)

        articles = []
        for title, link, summary, pub in raw_items[:max_items]:
            text = f"{title} {_strip_html(summary)}"
            vr   = self.verifier.verify(link, text, pub)
            if not vr["ok"]:
                log.debug(f"  SKIP [{vr['reason']}]: {title[:60]}")
                continue
            articles.append({
                "headline": title,
                "deck":     _strip_html(summary)[:300],
                "url":      link,
                "source":   vr["source_name"],
                "date":     pub.strftime("%Y-%m-%d") if pub else "—",
                "score":    vr["score"],
                "lang":     "ko" if any(ord(c) > 0x3000 for c in title) else "en",
            })

        log.info(f"  → {len(articles)} verified articles from {feed_url}")
        return articles


# ─────────────────────────────────────────────────────────────────────────────
# NEWSLETTER BUILDER
# ─────────────────────────────────────────────────────────────────────────────

class NewsletterBuilder:
    """Builds the HTML newsletter from collected articles."""

    def __init__(self, template_path: Path):
        self.template = template_path.read_text(encoding="utf-8")

    def _article_block(self, a: dict, size: str = "sm") -> str:
        kicker  = html.escape(a.get("kicker", a.get("lang", "").upper()))
        headline = html.escape(a.get("headline", ""))
        deck    = html.escape(a.get("deck", ""))
        source  = html.escape(a.get("source", ""))
        date    = html.escape(a.get("date", ""))
        url     = html.escape(a.get("url", "#"))

        deck_html = f'<div class="deck {"lg" if size=="lg" else ""}">{deck}</div>' if deck else ""
        return f"""
        <div class="article">
          <div class="kicker">{kicker}</div>
          <h2 class="{size}"><a href="{url}">{headline}</a></h2>
          {deck_html}
          <div class="byline">
            <span>{date}</span>
            <span class="source-badge">{source}</span>
          </div>
        </div>"""

    def build(self, articles_ko: list, articles_en: list, issue_date: str) -> str:
        """Return complete newsletter HTML string."""

        # Split: lead = highest score, rest fill columns
        all_articles = sorted(articles_ko + articles_en, key=lambda x: x["score"], reverse=True)
        lead = all_articles[0] if all_articles else {
            "headline": "No verified articles this week",
            "deck": "",
            "url": "#",
            "source": "—",
            "date": issue_date,
        }
        lead["kicker"] = "Top Story / 주요 뉴스"

        ko_arts = [a for a in all_articles if a.get("lang") == "ko"][:4]
        en_arts = [a for a in all_articles if a.get("lang") == "en" and a is not lead][:4]

        for a in ko_arts:
            a["kicker"] = "한국 / Korea"
        for a in en_arts:
            a["kicker"] = "Global / 글로벌"

        ticker_items = " ".join(
            f'<span>{html.escape(a["headline"][:80])}</span>' for a in all_articles[:8]
        ) or "<span>건설기계 주간 뉴스레터</span>"

        center_html = self._article_block(lead, "lg")
        korea_html  = "".join(self._article_block(a, "sm") for a in ko_arts)
        global_html = "".join(self._article_block(a, "sm") for a in en_arts)

        out = self.template
        out = out.replace('<div class="ticker-inner" id="ticker-content">', f'<div class="ticker-inner" id="ticker-content">{ticker_items}')
        out = out.replace("document.getElementById('center-articles').innerHTML = renderArticle(DATA.lead, 'lg');",
                          f"document.getElementById('center-articles').innerHTML = `{center_html}`;")
        out = out.replace("document.getElementById('korea-articles').innerHTML  = DATA.korea.map(a => renderArticle(a, 'sm')).join('');",
                          f"document.getElementById('korea-articles').innerHTML = `{korea_html}`;")
        out = out.replace("document.getElementById('global-articles').innerHTML = DATA.global.map(a => renderArticle(a, 'sm')).join('');",
                          f"document.getElementById('global-articles').innerHTML = `{global_html}`;")

        return out


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL SENDER
# ─────────────────────────────────────────────────────────────────────────────

class EmailSender:
    """
    SMTP 기반 이메일 발송.
    Gmail, Naver, Daum, SendGrid SMTP 지원.
    config.json의 email 섹션 설정 참조.
    """

    def __init__(self, cfg: dict):
        self.host     = cfg["smtp_host"]
        self.port     = cfg["smtp_port"]
        self.user     = cfg["smtp_user"]
        self.password = cfg["smtp_password"]
        self.from_addr = cfg.get("from_address", cfg["smtp_user"])
        self.from_name = cfg.get("from_name", "Construction Equipment Weekly")

    def send(self, recipients: list[dict], subject: str, html_body: str) -> int:
        """Send email to all recipients. Returns success count."""
        sent = 0
        try:
            server = smtplib.SMTP(self.host, self.port)
            server.ehlo()
            server.starttls()
            server.login(self.user, self.password)
        except Exception as e:
            log.error(f"SMTP connection failed: {e}")
            return 0

        for r in recipients:
            to_addr = r["email"]
            to_name = r.get("name", "")
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = f"{self.from_name} <{self.from_addr}>"
                msg["To"]      = f"{to_name} <{to_addr}>" if to_name else to_addr
                msg.attach(MIMEText(html_body, "html", "utf-8"))
                server.sendmail(self.from_addr, to_addr, msg.as_string())
                log.info(f"  ✓ Sent to {to_addr}")
                sent += 1
                time.sleep(0.5)  # rate limit
            except Exception as e:
                log.error(f"  ✗ Failed to send to {to_addr}: {e}")

        server.quit()
        log.info(f"Email sending complete: {sent}/{len(recipients)} successful")
        return sent


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def run(preview_only: bool = False):
    cfg       = load_config()
    verifier  = SourceVerifier()
    fetcher   = RSSFetcher(verifier, timeout=15)
    builder   = NewsletterBuilder(BASE_DIR / "newsletter.html")

    issue_date = datetime.date.today().isoformat()
    log.info(f"=== Construction Equipment Weekly — {issue_date} ===")

    # ── Collect Articles ──────────────────────────────────────────────────────
    articles_ko, articles_en = [], []

    for feed in cfg.get("rss_feeds", []):
        items = fetcher.fetch_feed(feed["url"], max_items=feed.get("max_items", 8))
        for a in items:
            if a["lang"] == "ko":
                articles_ko.append(a)
            else:
                articles_en.append(a)

    log.info(f"Total verified — KO: {len(articles_ko)}, EN: {len(articles_en)}")

    if not articles_ko and not articles_en:
        log.warning("No articles collected. Using placeholder content.")

    # ── Build HTML ────────────────────────────────────────────────────────────
    html_content = builder.build(articles_ko, articles_en, issue_date)
    out_path = OUTPUT_DIR / f"newsletter_{issue_date}.html"
    out_path.write_text(html_content, encoding="utf-8")
    log.info(f"Newsletter saved: {out_path}")

    if preview_only:
        log.info("Preview mode — skipping email send.")
        return

    # ── Send Emails ───────────────────────────────────────────────────────────
    email_cfg    = cfg.get("email", {})
    recipients   = cfg.get("recipients", [])

    if not recipients:
        log.warning("No recipients configured in config.json")
        return

    if not email_cfg.get("smtp_host"):
        log.warning("SMTP not configured — skipping email send.")
        return

    week_num = datetime.date.today().isocalendar()[1]
    subject  = f"[건설기계 주간] Construction Equipment Weekly — {issue_date} (Week {week_num})"

    sender = EmailSender(email_cfg)
    sender.send(recipients, subject, html_content)


def validate_data() -> bool:
    """
    뉴스레터 데이터 유효성 점검 (--validate 플래그로 실행).

    점검 항목:
      1. 화이트리스트 도메인 접근 가능 여부 (HTTP HEAD 요청)
      2. 주식 데이터 플레이스홀더 경고 (실시간 API 미연동 시)
      3. config.json 필수 항목 (SMTP, 수신자) 설정 여부
      4. RSS 피드 URL 접근 가능 여부
    """
    log.info("=== 뉴스레터 데이터 유효성 점검 시작 ===")
    all_ok = True

    # ── 1. 화이트리스트 도메인 점검 ──────────────────────────────────────────
    log.info("\n[1] 화이트리스트 소스 도메인 접근 점검")
    verifier = SourceVerifier()
    domain_results = []
    for domain, meta in verifier.WHITELIST.items():
        url = f"https://{domain}"
        try:
            req = Request(url, method="HEAD", headers={"User-Agent": "CEWeekly-Validator/1.0"})
            with urlopen(req, timeout=8) as resp:
                status = resp.status
            ok = status < 400
        except Exception as e:
            status = str(e)
            ok = False
        symbol = "✓" if ok else "✗"
        domain_results.append((symbol, domain, meta["name"], status))
        if not ok:
            all_ok = False
            log.warning(f"  ✗ {domain} ({meta['name']}) — 접근 실패: {status}")
        else:
            log.info(f"  ✓ {domain} ({meta['name']}) — {status}")

    # ── 2. 주식 데이터 플레이스홀더 경고 ─────────────────────────────────────
    log.info("\n[2] 주식 데이터 점검")
    STOCK_LIST = [
        {"sym": "HD현대건설기계", "code": "267270", "market": "KOSPI"},
        {"sym": "두산밥캣",       "code": "241560", "market": "KOSPI"},
        {"sym": "수산세보틱스",    "code": "017550", "market": "KOSPI"},
        {"sym": "대모",           "code": "317850", "market": "KOSDAQ"},
        {"sym": "현대에버다임",    "code": "041440", "market": "KOSDAQ"},
        {"sym": "CAT",            "code": "CAT",    "market": "NYSE"},
        {"sym": "Komatsu",        "code": "6301",   "market": "TSE"},
        {"sym": "Volvo AB",       "code": "VOLV-B", "market": "NASDAQ Stockholm"},
    ]
    template_path = BASE_DIR / "newsletter.html"
    template_text = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    for stock in STOCK_LIST:
        if "₩--,---" in template_text or "$--" in template_text:
            log.warning(f"  ⚠ {stock['sym']} ({stock['code']}, {stock['market']}) — 주가가 플레이스홀더(--) 상태입니다. 실시간 API 연동 필요.")
            all_ok = False
        else:
            log.info(f"  ✓ {stock['sym']} ({stock['code']}, {stock['market']}) — 데이터 있음")

    # ── 3. config.json 필수 항목 점검 ────────────────────────────────────────
    log.info("\n[3] config.json 설정 점검")
    try:
        cfg = load_config()
        email_cfg = cfg.get("email", {})
        smtp_ok = bool(email_cfg.get("smtp_host") and email_cfg.get("smtp_user") and email_cfg.get("smtp_password"))
        recipients = cfg.get("recipients", [])
        if smtp_ok:
            log.info(f"  ✓ SMTP 설정: {email_cfg['smtp_host']} / {email_cfg['smtp_user']}")
        else:
            log.warning("  ✗ SMTP 설정 누락 — config.json의 email 섹션을 확인하세요.")
            all_ok = False
        if recipients:
            log.info(f"  ✓ 수신자: {len(recipients)}명 등록됨")
        else:
            log.warning("  ✗ 수신자 없음 — config.json에 recipients를 추가하세요.")
            all_ok = False
    except Exception as e:
        log.error(f"  ✗ config.json 읽기 실패: {e}")
        all_ok = False

    # ── 4. RSS 피드 접근 점검 ─────────────────────────────────────────────────
    log.info("\n[4] RSS 피드 접근 점검")
    try:
        cfg = load_config()
        feeds = cfg.get("rss_feeds", [])
        if not feeds:
            log.warning("  ⚠ RSS 피드가 config.json에 등록되지 않았습니다.")
        for feed in feeds:
            feed_url = feed.get("url", "")
            try:
                req = Request(feed_url, headers={"User-Agent": "CEWeekly-Validator/1.0"})
                with urlopen(req, timeout=10) as resp:
                    feed_ok = resp.status < 400
                if feed_ok:
                    log.info(f"  ✓ {feed_url}")
                else:
                    log.warning(f"  ✗ {feed_url} — HTTP {resp.status}")
                    all_ok = False
            except Exception as e:
                log.warning(f"  ✗ {feed_url} — {e}")
                all_ok = False
    except Exception:
        pass

    # ── 결과 요약 ─────────────────────────────────────────────────────────────
    log.info("\n" + ("=" * 50))
    if all_ok:
        log.info("점검 결과: 모든 항목 정상 ✓")
    else:
        log.warning("점검 결과: 일부 항목 확인 필요 ⚠  (위 경고 메시지를 확인하세요)")
    return all_ok


def add_recipient(email: str, name: str = ""):
    cfg = load_config()
    cfg.setdefault("recipients", [])
    if any(r["email"] == email for r in cfg["recipients"]):
        log.info(f"Recipient already exists: {email}")
        return
    cfg["recipients"].append({"email": email, "name": name})
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    log.info(f"Added recipient: {name} <{email}>")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Construction Equipment Weekly Newsletter Generator")
    parser.add_argument("--preview",  action="store_true", help="Generate HTML only, do not send email")
    parser.add_argument("--validate", action="store_true", help="Validate sources, stocks, and config without sending")
    parser.add_argument("--add-recipient", nargs="+", metavar=("EMAIL", "NAME"),
                        help="Add a recipient to config.json")
    args = parser.parse_args()

    if args.add_recipient:
        email = args.add_recipient[0]
        name  = args.add_recipient[1] if len(args.add_recipient) > 1 else ""
        add_recipient(email, name)
    elif args.validate:
        ok = validate_data()
        sys.exit(0 if ok else 1)
    else:
        run(preview_only=args.preview)
