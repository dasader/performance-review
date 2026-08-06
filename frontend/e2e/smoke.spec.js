import { test, expect } from '@playwright/test'

// 이미 떠 있는 web(8103)을 상대로 돈다. 실행: ~/code/e2e-headless/run.sh http://localhost:8103 frontend/e2e
// 읽기 흐름만 검증한다(분석 실행은 관리자 키 + 외부 API 호출이라 제외).

test('첫 화면: 10대 분야 목록 렌더', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveTitle(/전략기술 논문성과 분석/)
  await expect(page.getByText('10대 전략기술 분야 성과 보고서')).toBeVisible()
  // 분야 카드는 /fields/:id 링크로 렌더된다
  await expect(page.locator('a[href^="/fields/"]')).toHaveCount(10)
})

test('분야 상세로 이동', async ({ page }) => {
  await page.goto('/')
  await page.locator('a[href^="/fields/"]').first().click()
  await expect(page).toHaveURL(/\/fields\/\d+$/)
  await expect(page.getByText('← 분야 목록')).toBeVisible()
  await expect(page.getByText('세부기술별 분석 현황')).toBeVisible()
})

test('관리자: 키 없이 접근하면 인증 화면', async ({ page }) => {
  await page.goto('/admin')
  await expect(page.getByText('관리자 인증')).toBeVisible()
  await expect(page.getByText('관리자 키가 있어야 접근할 수 있습니다')).toBeVisible()
})

test('첫 화면에 콘솔 에러 없음', async ({ page }) => {
  const errors = []
  page.on('console', m => m.type() === 'error' && errors.push(m.text()))
  page.on('pageerror', e => errors.push(String(e)))
  await page.goto('/', { waitUntil: 'networkidle' })
  expect(errors, '브라우저 콘솔 에러').toEqual([])
})
