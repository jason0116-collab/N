# SK스퀘어 NAV 할인율

SK스퀘어의 NAV(순자산가치) 할인율을 시각화하는 대시보드입니다. **GitHub Pages(정적) + GitHub Actions**만으로 동작하며, 별도 서버/백엔드가 필요 없습니다.

## 🏗️ 동작 구조

```
GitHub Actions (스케줄)                GitHub Pages (정적)
  ├─ 네이버 시세 조회                     sk_square_dashboard.html
  ├─ SK스퀘어 IR 파싱          ──생성──>   ├─ data/nav.json 읽기
  └─ NAV 계산 → JSON 커밋                 └─ data/history.json 읽기
```

GitHub Pages는 백엔드를 못 돌리므로, **GitHub Actions가 주기적으로 데이터를 받아 정적 JSON으로 커밋**하고 페이지는 그 JSON을 읽습니다. (실시간이 아니라 장중 약 15분 간격 갱신)

## 📊 데이터 소스

| 항목 | 소스 |
|------|------|
| SK스퀘어 / SK하이닉스 **주가** · **일별 시세** | 네이버 금융 (KOSPI 시세) |
| **하이닉스 외 자산가치** | SK스퀘어 IR (`sksquare.com/kor/ir/nav.do`) |
| **지분율 · 주식수** | 하드코딩 기준값 (`netlify/lib/nav.js`) |

## 🧮 NAV 계산

```
SK하이닉스 지분 시장가치 = 보유주식수 × SK하이닉스 종가
하이닉스 외 자산가치     = SK스퀘어 IR 페이지 파싱 (비상장 + 순현금)
총 NAV                  = 하이닉스 지분가치 + 하이닉스 외
주당 NAV                = 총 NAV / SK스퀘어 유통주식수
NAV 할인율              = (SK스퀘어 주가 − 주당 NAV) / 주당 NAV × 100
```

> 종목코드: SK스퀘어 `402340`, SK하이닉스 `000660` (지분율 20.07% / 보유 146,100,000주 / 발행 131,958,386주 — 변경 시 `netlify/lib/nav.js` 수정)

---

## 🚀 GitHub 배포 방법

### 1단계: 파일을 GitHub 저장소에 push

아래 파일/폴더가 모두 저장소에 있어야 합니다:
```
sk_square_dashboard.html         # 대시보드 (정적 JSON을 읽음)
data/nav.json, data/history.json # 초기 데이터 (Actions가 이후 자동 갱신)
scripts/generate-data.js         # 데이터 생성 스크립트
netlify/lib/nav.js               # 시세·IR·NAV 로직 (스크립트가 사용)
.github/workflows/update-data.yml# 자동 갱신 워크플로
```

### 2단계: GitHub Pages 켜기
저장소 **Settings → Pages → Source: Deploy from a branch → main / (root)** → Save
→ `https://<사용자>.github.io/<저장소>/sk_square_dashboard.html` 로 접속

### 3단계: Actions 활성화
- 저장소 **Actions** 탭에서 워크플로 활성화(처음엔 "I understand..." 버튼)
- **Update NAV data** 워크플로 → **Run workflow**로 즉시 1회 실행 가능
- 이후 평일 09:00~16:00(KST) **15분마다 자동 갱신** + 커밋

> 워크플로가 커밋하려면 **Settings → Actions → General → Workflow permissions → "Read and write permissions"** 가 켜져 있어야 합니다.

---

## 💻 로컬 미리보기

```bash
# 1) 데이터 생성 (Node 18+)
node scripts/generate-data.js
# 2) 정적 서버로 열기 (file:// 직접 열기는 안 됨)
python -m http.server 8000
#   → http://localhost:8000/sk_square_dashboard.html
```

---

## ⚠️ 주의 — 네이버 해외 IP 차단 가능성

GitHub Actions 실행 서버는 해외(미국)에 있어, **네이버가 그 IP를 막으면 주가가 0**으로 나올 수 있습니다. 그 경우 한국 리전 호스트(예: **Cloudtype**)에서 데이터를 생성하거나, 한국 IP에서 스크립트를 돌려 `data/*.json`을 커밋하는 방식으로 우회할 수 있습니다. (SK스퀘어 IR은 보통 문제없음)

---

## 📁 파일 구조

```
sk_square_dashboard.html          # 대시보드 (정적)
data/                             # Actions가 생성하는 JSON
  ├─ nav.json
  └─ history.json
scripts/generate-data.js          # 데이터 생성기
netlify/lib/nav.js                # 공유 로직 (시세·IR 파싱·NAV)
.github/workflows/update-data.yml # 자동 갱신
README.md
```

> 참고: `netlify/`, `sk_square_dashboard_backend.py`(Flask) 등은 다른 배포(Netlify/서버) 옵션용 잔여 파일로, GitHub Pages 배포에는 `netlify/lib/nav.js`만 사용됩니다.
