import { describe, expect, it } from "vitest";
import { lintQuery } from "./queryLint";

describe("lintQuery (openalex)", () => {
  it("허용되는 괄호·연산자 조합은 오류·경고 없음", () => {
    const r = lintQuery("(a OR b) AND c", "openalex");
    expect(r.errors).toHaveLength(0);
    expect(r.warnings).toHaveLength(0);
  });

  it("괄호가 닫히지 않으면 오류", () => {
    const r = lintQuery("(a OR b AND c", "openalex");
    expect(r.errors.some((e) => e.code === "paren-unclosed")).toBe(true);
  });

  it("닫는 괄호가 먼저 나오면 오류", () => {
    const r = lintQuery("a) OR (b", "openalex");
    expect(r.errors.some((e) => e.code === "paren-order")).toBe(true);
  });

  it("따옴표 짝이 안 맞으면 오류", () => {
    const r = lintQuery('a AND "b', "openalex");
    expect(r.errors.some((e) => e.code === "quote-unmatched")).toBe(true);
  });

  it("빈 괄호는 오류", () => {
    const r = lintQuery("()", "openalex");
    expect(r.errors.some((e) => e.code === "empty-parens")).toBe(true);
  });

  it("선행 연산자는 오류", () => {
    const r = lintQuery("AND foo", "openalex");
    expect(r.errors.some((e) => e.code === "leading-operator")).toBe(true);
  });

  it("후행 연산자는 오류", () => {
    const r = lintQuery("foo OR", "openalex");
    expect(r.errors.some((e) => e.code === "trailing-operator")).toBe(true);
  });

  it("연산자가 연속되면 오류", () => {
    const r = lintQuery("a AND OR b", "openalex");
    expect(r.errors.some((e) => e.code === "consecutive-operators")).toBe(true);
  });

  it("AND NOT은 허용되는 부정 결합이라 오류 없음", () => {
    const r = lintQuery("a AND NOT b", "openalex");
    expect(r.errors).toHaveLength(0);
  });

  it("콤마는 경고", () => {
    const r = lintQuery("a, b", "openalex");
    expect(r.warnings.some((w) => w.code === "comma-pipe")).toBe(true);
  });

  it("파이프는 경고", () => {
    const r = lintQuery("a | b", "openalex");
    expect(r.warnings.some((w) => w.code === "comma-pipe")).toBe(true);
  });

  it("빈 문자열은 오류·경고 없음", () => {
    const r = lintQuery("", "openalex");
    expect(r.errors).toHaveLength(0);
    expect(r.warnings).toHaveLength(0);
  });
});

describe("lintQuery (kci)", () => {
  it("KCI 모드는 연산자 검사를 하지 않는다", () => {
    const r = lintQuery("a AND OR b", "kci");
    expect(r.errors).toHaveLength(0);
  });
});
