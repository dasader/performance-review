const { test, expect } = require("@playwright/test");

const wait = (page, ms) => page.waitForTimeout(ms);
// 캔버스만 잘라 찍는다. 전체 화면을 찍으면 캐럿 깜빡임·스크롤 힌트 같은
// 시간 기반 애니메이션이 섞여 "멈췄다"를 영영 관측할 수 없다.
const canvasShot = (page, id) => page.locator(`#${id}`).screenshot();

const gotoAct = (page, act, at) =>
  page.evaluate(
    ([act, at]) => {
      const el = document.querySelector(`[data-act="${act}"]`);
      window.scrollTo(0, el.offsetTop + Math.max(0, el.offsetHeight - innerHeight) * at);
    },
    [act, at]
  );

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/landing-mock.html");
  await wait(page, 500);
});

test("관성 — 스크롤이 멈춘 뒤에도 데이터가 따라오다 멈춘다", async ({ page }) => {
  await gotoAct(page, "read", 0.55);

  await wait(page, 40);
  const a = await canvasShot(page, "c-read");
  await wait(page, 700);
  const b = await canvasShot(page, "c-read");
  expect(Buffer.compare(a, b), "손을 뗀 뒤에도 점이 계속 자리를 잡아야 한다").not.toBe(0);

  await wait(page, 600);
  const c = await canvasShot(page, "c-read");
  await wait(page, 500);
  const d = await canvasShot(page, "c-read");
  expect(Buffer.compare(c, d), "그리고 저절로 멈춰야 한다").toBe(0);
});

test("속도 제한 — 확 밀어도 순간이동하지 않는다", async ({ page }) => {
  await gotoAct(page, "read", 0.5);   // 맨 위에서 수천 px 을 한 번에 건너뛴다

  // 화면 픽셀을 직접 비교한다 — toDataURL().length 로 재다가 다른 그림이 같은
  // 바이트 길이로 압축돼 통과를 놓쳤다. 길이는 그림이 같다는 증거가 못 된다.
  await wait(page, 70);
  const a = await canvasShot(page, "c-read");
  await wait(page, 1200);
  const b = await canvasShot(page, "c-read");
  expect(Buffer.compare(a, b), "점프 직후에도 화면이 계속 움직여야 한다").not.toBe(0);
});

test("흔들림 없음 — 고정된 활자는 스크롤 중에 되돌아오지 않는다", async ({ page }) => {
  // 회귀 방지. 고정된 막에 속도 비례 밀림(--lag)을 걸었더니 휠 한 칸마다
  // 0→32→8→32px 를 반복하며 글자가 떨렸다(실측 6칸에 방향 전환 11회).
  await gotoAct(page, "read", 0.25);
  await wait(page, 1500);

  await page.evaluate(() => {
    window.__s = [];
    const kw = document.querySelector('[data-act="read"] .keyword');
    const tick = () => {
      window.__s.push(+kw.getBoundingClientRect().top.toFixed(1));
      if (window.__s.length < 90) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  for (let i = 0; i < 6; i++) {
    await page.mouse.wheel(0, 100);
    await wait(page, 90);
  }
  await wait(page, 900);

  const tops = await page.evaluate(() => window.__s);
  // 스크롤은 한 방향인데 세로 위치가 방향을 바꾸면 그게 떨림이다
  let flips = 0, dir = 0;
  for (let i = 1; i < tops.length; i++) {
    const d = tops[i] - tops[i - 1];
    if (Math.abs(d) <= 0.5) continue;
    const nd = Math.sign(d);
    if (dir && nd !== dir) flips++;
    dir = nd;
  }
  expect(flips, `세로 방향 전환 ${flips}회 — 0이어야 한다`).toBe(0);
  expect(Math.max(...tops) - Math.min(...tops), "세로 진폭").toBeLessThanOrEqual(1);
});

test("상단 바로 소개를 건너뛰고 대시보드로 들어갈 수 있다", async ({ page }) => {
  // 같은 문구가 7막 CTA 에도 있으므로 상단 바로 좁힌다
  const enter = page.locator("#topbar").getByRole("link", { name: /분야 목록 열기/ });
  await expect(enter, "첫 화면부터 진입 링크가 보인다").toBeVisible();
  await expect(enter).toHaveAttribute("href", "/");

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await wait(page, 1400);
  await expect(enter, "스크롤 내내 남는다").toBeVisible();
  await expect(page.locator("#topbar")).toHaveAttribute("data-on", "paper");
  await page.screenshot({ path: "/out/topbar-paper.png" });
});

test("모션 최소화 설정에서는 감쇠가 없다", async ({ browser }) => {
  // browser.newPage 는 config 의 baseURL 을 물려받지 않는다 — 직접 넘겨야
  // run.sh 가 준 대상(E2E_BASE_URL)을 따라간다. 주소를 박아 두면 다른 대상에
  // 물렸을 때 이 케이스만 조용히 옛 서버를 때린다.
  const page = await browser.newPage({
    reducedMotion: "reduce",
    baseURL: process.env.E2E_BASE_URL,
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/landing-mock.html");
  await wait(page, 500);
  await gotoAct(page, "read", 0.55);

  await wait(page, 60);
  const a = await canvasShot(page, "c-read");
  await wait(page, 700);
  const b = await canvasShot(page, "c-read");
  expect(Buffer.compare(a, b), "reduce 에서는 즉시 최종 상태여야 한다").toBe(0);
  await page.close();
});
