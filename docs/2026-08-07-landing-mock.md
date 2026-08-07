# 랜딩페이지 시안 (Apple 제품 페이지 화법)

`frontend/public/landing-mock.html` + `frontend/public/landing-mock.js`

```bash
docker compose up -d --build web        # → http://localhost:8103/landing-mock.html
```

`public/`은 Vite가 가공 없이 내보내고 nginx의 `try_files`도 실제 파일을 먼저 찾으므로,
빌드 설정이나 라우트 추가 없이 열린다. 시안이 번들에 섞이는 게 싫으면 두 파일을 지우면 된다.

## 왜 필요한가

지금 `/`는 랜딩이 아니라 도구다(`FieldList`). 처음 온 사람이 보는 첫 화면이 분야 목록이라
"이 서비스가 무엇을 하는가"를 말할 자리가 없다. 랜딩을 붙인다면 `/`를 랜딩으로,
분야 목록을 `/fields`로 옮기는 형태가 된다.

## 서사

**한 해 1만 편의 초록을 전부 읽는 일은 원래 불가능했고, 이제 가능해졌다.**
그래서 근거로 전략기술 연구동향을 말할 수 있고, 로드맵 목표와 한 줄씩 대조할 수 있다.

| 막 | 키워드 | 무엇을 말하나 | 장치 |
|---|---|---|---|
| 1 | *한 해 논문 1만 편 / 아무도 다 읽지 못했다* | 문제 제기 | Canvas 점 구름 → 산점도 |
| 2 | 전수 | 검색 12,376 → 초록 보유 10,740 전수 분석 | 키워드 + 수치 4개 |
| 3 | 판독 | 초록에서 `outcome`·`approach`·`improvement`·`metrics[]` 추출 | Canvas 4군집 + 항목표 |
| 4 | 근거 | 각주가 논문을 가리킨다 · 3단 reduce 중간 보고서 보존 | 보고서 발췌 + 각주 |
| 5 | 대조 | **로드맵 목표 65행 전수 점검 + 한계 고지** | 판정 4단 + 한계 박스 |
| 6 | 55 | 10대 분야 55개 세부기술, 검색식 고정 = 재현 가능 | 격자 stagger (핀 없음) |
| 7 | (밝은 면) 어느 분야부터 보시겠어요 | 도구로 인계 | 앱 토큰 전환 + View Transition |

5막이 이 서비스의 차별점이다. 판정 4단(`관련 연구 확인`/`부분 관련`/`데이터 없음`/`분석 범위 밖`)을
그대로 쓰고, **"논문에 실리지 않는 것은 논문으로 답할 수 없다"**는 한계를 같은 화면에서 밝힌다 —
`ROADMAP_CHECK_INSTRUCTION`의 "### 4. 이 점검의 한계"가 존재하는 이유와 같다.

**수치는 실제 값이다.** 분야·세부기술 수와 논문 건수는 `GET /api/fields`와
`/api/fields/{id}/summary?year=2026`에서 가져왔다(2026년 기준 55/55 완료).
보고서 발췌·각주·판정 4행만 형식을 보이기 위한 예시이며 화면에 그렇게 표기했다.

## 제약 — 스크립트를 인라인하지 말 것

**nginx CSP가 `script-src 'self'`다**(`frontend/nginx.conf`). 인라인 `<script>`는 차단되므로
JS는 반드시 `landing-mock.js` 같은 **같은 출처의 별도 파일**이어야 한다. 인라인으로 되돌리면
화면이 정지 이미지가 되고 e2e의 "콘솔 에러 없음" 케이스가 CSP 위반으로 깨진다.
`<style>`과 `style=` 속성은 `style-src 'unsafe-inline'`이라 그대로 써도 된다.
폰트 두 출처(jsdelivr·Google Fonts)도 CSP에 이미 허용돼 있다.

## 디자인 — 이 저장소의 규칙을 무대까지 가져왔다

- **accent는 무채색이다.** 크롬(상단 바·진행 바·브랜드 마크)에 채도를 두지 않는다.
  색은 데이터 계열(`d1`~`d4`)과 판정색만 갖는다. 무대가 어두워 명도만 올린 변주를 쓴다.
- **mono는 한글이 섞인 문자열에 쓰지 않는다.** 눈썹 라벨·판정·캡션은 전부 Pretendard이고,
  JetBrains Mono는 숫자·DOI·필드명 같은 라틴 슬롯에만 쓴다.
- **모서리 3종**(면 0 / 조작물 2px / 표식 999px)과 `--maxw` 1180px을 지킨다.
- **활자 크기는 앱 스케일을 따르지 않는다.** 11/12/14/16/20/26은 화면용이고 무대는 그 위로
  두 단을 더 쓴다. 이 시안의 키워드 크기를 앱 안으로 가져가지 말 것.

## 모션 — 의존성 0

GSAP·Framer Motion·Lenis 없이 네이티브만 쓴다.

- **화면 고정**: `position: sticky` + 긴 트랙.
- **스크롤 구동**: `view-timeline-name` + `animation-timeline`. `@supports`로 감싸
  미지원 브라우저(Firefox 등)는 연출 없이 최종 상태로 전부 읽힌다.
- **화면전환**: `document.startViewTransition`.
- **접근성**: `prefers-reduced-motion`에서 모션 전면 정지.

### 관성은 데이터에만 건다

스크롤은 가로채지 않는다. 휠을 `preventDefault`로 뺏으면 트랙패드 관성·키보드·터치·
스크롤바가 전부 자체 구현으로 갈아끼워지고, 하나라도 빠뜨리면 접근성이 깨진다.
네이티브 스크롤은 두고 **연출이 읽는 값만** 감쇠한다.

```js
d = clampBehind(scrollY - lagged)              // 1.35화면 밖으로는 안 벌어진다
lagged += clamp(d * K, -MAX_STEP, MAX_STEP)    // K=0.22(민감도) · 46px/프레임(속도 제한)
```

감쇠된 값은 **캔버스만** 읽는다. 고정된 막에도 속도 비례 밀림을 걸었다가 걷어냈다 —
휠 한 칸마다 글자가 위아래로 떨렸다(실측 6칸에 세로 방향 전환 11회, 진폭 32px → 제거 후 0회).
캔버스가 늦게 자리를 잡는 것은 관성이지만 글자를 붙잡은 틀이 흔들리는 것은 떨림이다.
`prefers-reduced-motion`에서는 `K`뿐 아니라 `MAX_STEP`도 함께 푼다 — 상한만 남기면
"모션 없음"이 아니라 프레임당 46px씩 기어가는 "느린 모션"이 된다.

진행도 계산에는 `offsetTop`/`offsetHeight`만 쓴다. `getBoundingClientRect`를 쓰면 화면에 걸린
transform을 되읽어 연출이 자기 결과를 다시 입력으로 먹는다.

## 검증

시안은 아직 `web` 컨테이너에 빌드되지 않았으므로, **실제 CSP 헤더를 붙인 정적 서버**로 검증했다.

```bash
python3 - <<'PY' &   # nginx.conf의 CSP를 그대로 얹는다 — 안 얹으면 인라인 차단 같은 문제를 놓친다
import functools, http.server
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP); super().end_headers()
http.server.HTTPServer(("0.0.0.0", 8925), functools.partial(H, directory="frontend/public")).serve_forever()
PY
~/code/e2e-headless/run.sh http://localhost:8925 <스펙디렉터리>
```

6/6 통과 — 관성 정착·속도 제한·**세로 떨림 0회**·상단 진입 링크·밝은 면 바 반전·
`reduced-motion` 무감쇠, 그리고 7막 전 구간 스크린샷(가로 오버플로 0 · 콘솔 에러 0).
`web`을 다시 빌드한 뒤에는 `http://localhost:8103/landing-mock.html`로 같은 스펙을 돌리면 된다.
