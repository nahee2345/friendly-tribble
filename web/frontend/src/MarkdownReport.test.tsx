import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownReport, reportInlineText } from "./MarkdownReport";

describe("보고서 화면 변환", () => {
  it("저장 Markdown은 바꾸지 않고 화면에서 기호와 엔티티를 한 번만 해제한다", () => {
    const source = "## **요약**\n- \\[계좌번호 형태 마스킹\\]\n\n| 항목 | 값 |\n|---|---|\n| 상태 | &lt;확인&gt; &amp; 안전 |";
    render(<MarkdownReport markdown={source} />);
    expect(screen.getByRole("heading", { name: "요약" })).toBeInTheDocument();
    expect(screen.getByText("[계좌번호 형태 마스킹]")).toBeInTheDocument();
    expect(screen.getByText("<확인> & 안전")).toBeInTheDocument();
    expect(screen.queryByText("|---|")).not.toBeInTheDocument();
    expect(source).toContain("**요약**");
  });

  it("재귀 엔티티 해제를 하지 않는다", () => {
    expect(reportInlineText("&amp;lt;script&amp;gt;")).toBe("&lt;script&gt;");
  });
});
