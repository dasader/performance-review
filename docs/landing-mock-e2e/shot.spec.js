const { test } = require("@playwright/test");

// 막마다 핀이 물린 한가운데로 스크롤해 찍는다.
test("막별 스크린샷", async ({ page }) => {
  const errs = [];
  page.on("console", (m) => m.type() === "error" && errs.push(m.text()));
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/landing-mock.html");
  await page.waitForTimeout(400);

  const keys = await page.$$eval("[data-act]", (els) => els.map((e) => e.dataset.act));
  for (const k of keys) {
    for (const at of [0.15, 0.55, 0.9]) {
      await page.evaluate(([k, at]) => {
        const el = document.querySelector(`[data-act="${k}"]`);
        const span = Math.max(0, el.offsetHeight - innerHeight);
        window.scrollTo(0, el.offsetTop + span * at);
      }, [k, at]);
      await page.waitForTimeout(400);
      await page.screenshot({ path: `/out/${k}-${at}.png` });
    }
  }

  // 가로 스크롤이 생기면 레이아웃이 깨진 것
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);

  // 화면전환 확인
  await page.getByRole("button", { name: "양자", exact: true }).click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: "/out/zz-transition.png" });

  if (overflow > 1) throw new Error(`가로 오버플로 ${overflow}px`);
  if (errs.length) throw new Error("콘솔 에러: " + errs.join(" | "));
});
