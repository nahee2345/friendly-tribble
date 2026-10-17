import { describe, expect, it } from "vitest";
import { canonicalPercent, compareDecimal, parsePercent, sumPercentages, totalGuidance } from "./decimal";

describe("정확한 전략 비중 계산", () => {
  it.each([
    [["25", "15", "15", "10", "15", "10", "5", "5"], true, "100"],
    [["25", "15", "15", "10", "15", "10", "5", "4.9"], false, "99.9"],
    [["25", "15", "15", "10", "15", "10", "5", "5.1"], false, "100.1"],
    [["12.5", "12.5", "12.5", "12.5", "12.5", "12.5", "12.5", "12.5"], true, "100"]
  ])("부동소수점 없이 %j 합계를 판정한다", (weights, valid, total) => {
    const result = totalGuidance(weights);
    expect(result.valid).toBe(valid);
    expect(result.total).toBe(total);
  });

  it("미세한 경계를 반올림하지 않는다", () => {
    expect(totalGuidance(["99.999999", "0.000001"]).valid).toBe(true);
    expect(totalGuidance(["99.999999", "0"]).valid).toBe(false);
    expect(totalGuidance(["100", "0.000001"]).message).toContain("0.000001%를 줄여야");
  });

  it.each(["", "-1", "101", "1e2", "NaN", "Infinity", "1,0", " 10 ", "10.1234567"])("비정상 입력 %s을 거부한다", (value) => {
    expect(parsePercent(value)).toBeNull();
  });

  it("표시 정규화와 서로 다른 소수 자릿수 비교를 정확히 한다", () => {
    expect(canonicalPercent("010")).toBeNull();
    expect(canonicalPercent("10.5000")).toBe("10.5");
    expect(sumPercentages(["0.1", "0.2", "99.7"])).toBe("100");
    expect(compareDecimal("99.99", "100")).toBe(-1);
  });
});
