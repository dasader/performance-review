"use strict";
/* 전략기술 논문성과 분석 · 랜딩 시안
   별도 파일인 이유: nginx CSP 가 script-src 'self' 라 인라인 스크립트가 차단된다
   (frontend/nginx.conf). 같은 출처의 파일이어야 실행되고, 그래야 e2e 의
   "콘솔 에러 없음" 케이스도 통과한다. */

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

const topbar = document.getElementById("topbar");
const bench = document.getElementById("bench");

/* ── 관성 ─────────────────────────────────────────────────────────────────
   스크롤 자체는 가로채지 않는다. 휠을 preventDefault 로 뺏는 순간 트랙패드
   관성·키보드·터치·스크롤바가 전부 우리 구현으로 갈아끼워지고, 하나라도
   빠뜨리면 접근성이 깨진다. 네이티브 스크롤은 두고 **연출이 읽는 값만** 감쇠한다.

   감쇠된 값은 캔버스만 읽는다. 고정된 막에 속도 비례 밀림을 걸었다가 걷었다 —
   휠 한 칸마다 글자가 위아래로 떨렸다. 관성은 데이터에 있어야 하고, 글자를
   붙잡은 틀이 흔들리면 그건 떨림이다. */
const K = reduced ? 1 : 0.22;               // 따라잡는 속도(민감도)
const MAX_STEP = reduced ? Infinity : 46;   // 프레임당 이동 상한 ≈ 2,760px/s
// reduce 에서 상한만 남기면 "모션 없음"이 아니라 프레임당 46px 씩 기어가는
// "느린 모션"이 된다. K 와 함께 풀어야 한다.
const MAX_BEHIND = 1.35;                    // 뒤처짐 상한(화면 높이 배수)
const drawers = [];
let lagged = scrollY;
let alive = false;

function frame() {
  const y = scrollY;
  let d = y - lagged;

  // 스크롤바를 끝까지 끌거나 End 를 누르면 속도 제한만으로는 화면이 몇 초씩
  // 뒤에서 기어온다. 1.35화면 밖으로 벌어지면 끌어당겨, 따라잡는 시간이 이동
  // 거리와 무관하게 일정해진다.
  const behind = innerHeight * MAX_BEHIND;
  if (Math.abs(d) > behind) { lagged = y - Math.sign(d) * behind; d = Math.sign(d) * behind; }

  lagged += Math.max(-MAX_STEP, Math.min(MAX_STEP, d * K));
  d = y - lagged;

  // 0.4px 이하는 화면에서 구분되지 않는다. 더 조이면 보이지도 않는 꼬리를
  // 쫓느라 rAF 루프만 1초 가까이 더 돈다.
  if (Math.abs(d) < 0.4) { lagged = y; alive = false; }

  for (const f of drawers) f(lagged);
  topbar.dataset.solid = y > 48 ? "1" : "0";
  topbar.dataset.on = y + 56 >= bench.offsetTop ? "paper" : "stage";
  if (alive) requestAnimationFrame(frame);
}
function wake() { if (!alive) { alive = true; requestAnimationFrame(frame); } }
addEventListener("scroll", wake, { passive: true });
addEventListener("resize", wake);

/* ── Canvas 좌표 필드 — 히어로와 판독이 같은 엔진을 쓴다 ─────────────────
   점마다 (시작, 끝) 좌표를 갖고 감쇠된 스크롤 진행도로 보간한다. */
function field(canvas, sectionEl, layout) {
  const ctx = canvas.getContext("2d");
  let w = 0, h = 0, pts = [];

  function resize() {
    const r = canvas.getBoundingClientRect();
    const dpr = Math.min(devicePixelRatio || 1, 2);
    w = r.width; h = r.height;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    pts = layout(w, h);
    draw(lagged);
  }
  // 진행도는 offsetTop 으로 계산한다 — 화면에 걸린 transform 을 되읽지 않아,
  // 연출이 자기 결과를 다시 입력으로 먹는 일이 없다.
  function draw(sy) {
    const span = sectionEl.offsetHeight - innerHeight;
    const p = span <= 0 ? 0 : Math.min(1, Math.max(0, (sy - sectionEl.offsetTop) / span));
    ctx.clearRect(0, 0, w, h);
    const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;  // easeInOutCubic
    for (const pt of pts) {
      ctx.globalAlpha = pt.a0 + (pt.a1 - pt.a0) * e;
      ctx.fillStyle = e > 0.45 ? pt.c : "#8b8b95";
      ctx.beginPath();
      ctx.arc(pt.x0 + (pt.x1 - pt.x0) * e, pt.y0 + (pt.y1 - pt.y0) * e, pt.r, 0, 6.284);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  drawers.push(draw);
  new ResizeObserver(() => { resize(); wake(); }).observe(canvas);
  resize();
}

// 데이터 계열 d1..d4 — 어두운 지면에서 읽히도록 명도만 올린 값. 순서 고정.
const D = ["#5c9ce6", "#f2895c", "#35cd97", "#f2b93b"];
// 결정론적 난수 — 새로고침마다 배치가 흔들리지 않는다
let seed = 20260807;
const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;

/* 히어로: 흩어진 구름 → 연도축을 따라 오르는 산점도 */
field(document.getElementById("c-hero"), document.getElementById("hero"), (w, h) => {
  seed = 20260807;
  const n = w < 700 ? 260 : 620;
  const px = w * 0.5, py = h * 0.52;
  const sw = Math.min(w * 0.62, 780), sh = Math.min(h * 0.58, 460);
  return Array.from({ length: n }, () => {
    const t = rnd(), j = (rnd() - 0.5) * 0.42;
    return {
      x0: rnd() * w, y0: rnd() * h, a0: 0.34, r: rnd() * 1.9 + 0.9,
      x1: px - sw / 2 + t * sw,
      y1: py + sh / 2 - (Math.pow(t, 0.78) + j) * sh * 0.82,
      a1: 0.28 + rnd() * 0.5,
      c: D[0],
    };
  });
});

/* 판독: 균질한 구름 → 성과 유형 네 덩어리. 색이 곧 유형이다 */
field(document.getElementById("c-read"), document.querySelector('[data-act="read"]'), (w, h) => {
  seed = 991;
  const n = w < 700 ? 200 : 460;
  const cx = w * 0.74, cy = h * 0.5;
  const sp = Math.min(w * 0.17, h * 0.22, 185);
  return Array.from({ length: n }, () => {
    const q = (rnd() * 4) | 0;
    const dx = q === 1 || q === 3 ? 1 : -1;
    const dy = q < 2 ? -1 : 1;
    return {
      x0: cx + (rnd() - 0.5) * sp * 2.1, y0: cy + (rnd() - 0.5) * sp * 2.1,
      a0: 0.3, r: rnd() * 2.1 + 1,
      x1: cx + dx * sp * (0.55 + rnd() * 0.85),
      y1: cy + dy * sp * (0.55 + rnd() * 0.85),
      a1: 0.45 + rnd() * 0.35,
      c: D[q],
    };
  });
});

/* ── 6막 격자 — 실제 55개 세부기술 ────────────────────────────────────── */
const SUBFIELDS = [
  "AI 인프라 고도화", "효율적 AI 학습 및 추론", "첨단 AI모델링·의사결정", "안전·신뢰 AI",
  "버티컬 AI", "로봇 부품·플랫폼", "로봇 지능기술", "AI 제조", "자율주행 시스템",
  "데이터·AI 보안", "디지털 취약점 분석·침해대응", "산업보안·블록체인", "6G",
  "5G 고도화(5G-Adv)", "위성통신", "AI-네트워크", "차세대 통신부품", "차세대 고성능 센싱",
  "차세대 메모리반도체", "고성능·저전력 인공지능 반도체", "반도체 첨단패키징",
  "화합물 전력반도체", "국방반도체", "반도체 소재·부품·장비", "무기발광 디스플레이",
  "차세대 OLED", "디스플레이 소재·부품·장비", "합성생물학·바이오제조", "세포·유전자 치료",
  "차세대 백신", "바이오 데이터·인공지능", "바이오 인공장기·혈액",
  "뇌-컴퓨터 인터페이스(BCI)", "그린바이오", "리튬이온전지", "차세대 이차전지",
  "에너지저장시스템(ESS)", "재사용발사체", "위성시스템·탑재체", "우주관측·탐사",
  "첨단 항공 가스터빈 엔진·부품", "드론·도심항공교통(UAM)", "친환경·자율운항 선박",
  "혁신·지속가능 소재", "미래소재 및 설계·평가 플랫폼", "청정수소 생산·저장·운송·활용",
  "소형 모듈형 원자로(SMR)", "선진원자력시스템 및 폐기물 관리", "핵융합", "지능형 전력망",
  "재생에너지", "탄소 포집·활용·저장(CCUS)", "양자컴퓨팅", "양자통신", "양자센싱",
];
// 세부기술 → 분야 인덱스. 격자의 색띠가 분야 경계를 그린다.
const FIELD_OF = [0,0,0,0,0, 1,1,1,1, 2,2,2,2,2,2,2,2, 3,3,3,3,3,3,3,3,3,3,
  4,4,4,4,4,4,4, 5,5,5, 6,6,6,6,6,6, 7,7, 8,8,8,8,8,8,8, 9,9,9];

const revealObs = new IntersectionObserver((es) => {
  for (const e of es) if (e.isIntersecting) { e.target.classList.add("seen"); revealObs.unobserve(e.target); }
}, { rootMargin: "0px 0px -12% 0px" });

const grid = document.getElementById("tech-grid");
grid.append(...SUBFIELDS.map((name, i) => {
  const d = document.createElement("div");
  d.className = "tech-cell reveal";
  d.style.boxShadow = `inset 2px 0 0 ${D[FIELD_OF[i] % 4]}`;
  d.style.transitionDelay = reduced ? "0ms" : `${Math.min(i, 34) * 22}ms`;
  const n = document.createElement("span");
  n.className = "n";
  n.textContent = String(i + 1).padStart(2, "0");
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = name;   // textContent — 이름에 괄호·중점이 섞여 있어 innerHTML 로 넣지 않는다
  d.append(n, t);
  return d;
}));
document.querySelectorAll(".reveal").forEach((el) => revealObs.observe(el));

/* ── 7막 화면전환 (View Transitions API) — 2026년 실제 분석 결과 ────────── */
const FIELDS = [
  { name: "반도체·디스플레이", searched: 1284, analyzed: 1143, subs: 10 },
  { name: "첨단바이오",       searched: 2224, analyzed: 1963, subs: 7 },
  { name: "인공지능",         searched: 1374, analyzed: 1218, subs: 5 },
  { name: "양자",             searched: 363,  analyzed: 307,  subs: 3 },
];
const panel = document.getElementById("panel");
const fmt = (n) => n.toLocaleString("ko-KR");

function render(i) {
  const f = FIELDS[i];
  panel.replaceChildren(...[
    ["검색된 논문", fmt(f.searched), "2026년 · 한국 소속"],
    ["전수 분석", fmt(f.analyzed), "초록을 가진 논문 전부"],
    ["초록 미보유", fmt(f.searched - f.analyzed), "회수 실패분 · 건수로 공개"],
    ["세부기술", String(f.subs), "각각 보고서 한 편"],
  ].map(([label, value, sub]) => {
    const box = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    const s = document.createElement("div");
    s.className = "sub";
    s.textContent = sub;
    dd.append(s);
    box.append(dt, dd);
    return box;
  }));
}
render(0);

for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => {
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
    const go = () => render(+chip.dataset.f);
    // 네이티브 화면전환. 미지원 브라우저는 그냥 즉시 교체된다.
    if (document.startViewTransition && !reduced) document.startViewTransition(go); else go();
  });
}

// 첫 프레임 한 번 — 새로고침으로 중간부터 열렸을 때 상단 바와 캔버스를 맞춘다
wake();
