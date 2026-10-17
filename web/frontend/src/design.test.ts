import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("승인 Wealthy 디자인 토큰", () => {
  it("라이트 토큰과 공통 프레임 치수를 유지한다", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    for (const token of ["#F7F8FA", "#161B23", "#6C7480", "#0E9873", "#D84B54"]) {
      expect(css).toContain(token);
    }
    expect(css).toMatch(/\.sidebar\s*\{[^}]*width:\s*220px/s);
    expect(css).toMatch(/\.topbar\s*\{[^}]*height:\s*74px/s);
    expect(css).toContain("--card-radius: 12px");
    expect(css).toContain(':root[data-theme="dark"]');
  });
});
