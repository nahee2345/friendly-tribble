from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path

from asset_assistant.cli import run
from asset_assistant.domain import DataRealm, DomainValidationError
from asset_assistant.reporting import build_markdown_report
from asset_assistant.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "offline_sample.json"


def sample_data() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def write_data(directory: str, data: dict, name: str = "input.json") -> Path:
    path = Path(directory) / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def write_text(directory: str, text: str, name: str = "input.json") -> Path:
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


class ScenarioTests(unittest.TestCase):
    def assert_cli_input_error_without_echo(
        self,
        argv: list[str],
        *,
        forbidden: tuple[str, ...] = (),
    ) -> str:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                run(argv)
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        message = stderr.getvalue()
        self.assertNotIn("Traceback", message)
        for value in forbidden:
            self.assertNotIn(value, message)
        return message

    def test_sample_is_explicitly_offline_and_hypothetical(self) -> None:
        scenario = load_scenario(SAMPLE)
        self.assertEqual(scenario.mode, "OFFLINE")
        self.assertEqual(scenario.realm, DataRealm.SAMPLE)
        self.assertEqual(scenario.declared_initial_assets.amount_krw, 4_000_000)
        report = build_markdown_report(scenario)
        self.assertIn("OFFLINE / 가상 샘플", report)
        self.assertIn("실제 잔고가 아닙니다", report)
        self.assertIn("상세 원장과 **합산하지 않고 대조만 함**", report)
        self.assertIn("주문안: 없음", report)
        self.assertIn("수동 원장 보고서는 계좌 조회·주문 실행 모듈과 연결되지 않습니다", report)
        self.assertNotIn("첫 버전은 OFFLINE 전용", report)
        self.assertIn("실제 세후 수령액: 미입력", report)

    def test_report_preserves_required_section_order(self) -> None:
        report = build_markdown_report(load_scenario(SAMPLE))
        headings = [
            "## 1) 요약 3줄",
            "## 2) 자산 현황표",
            "## 3) 목표 대비",
            "## 4) 배당",
            "## 5) 위성",
            "## 6) 주문안과 이유",
            "## 7) 위반·주의",
        ]
        offsets = [report.index(heading) for heading in headings]
        self.assertEqual(offsets, sorted(offsets))

    def test_non_offline_mode_is_rejected(self) -> None:
        data = sample_data()
        data["mode"] = "LIVE"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DomainValidationError):
                load_scenario(write_data(temporary, data))

    def test_unknown_field_is_rejected_instead_of_becoming_a_secret_field(self) -> None:
        data = sample_data()
        data["accounts"][0]["account_number"] = "not-a-real-number"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DomainValidationError):
                load_scenario(write_data(temporary, data))

    def test_missing_value_remains_incomplete_not_zero(self) -> None:
        data = sample_data()
        data["positions"][0]["value_status"] = "MISSING"
        data["positions"][0]["market_value_krw"] = None
        with tempfile.TemporaryDirectory() as temporary:
            scenario = load_scenario(write_data(temporary, data))
            aggregate = scenario.ledger.investment_value()
            self.assertFalse(aggregate.complete)
            self.assertNotEqual(aggregate.amount_krw, 4_000_000)
            report = build_markdown_report(scenario)
            self.assertIn("정확한 배분 표시 불가", report)

    def test_satellite_cap_report_has_distinct_boundary_states(self) -> None:
        cases = (
            (0, "상한 미만·매수 의무 없음"),
            (400_000, "상한 도달"),
            (400_400, "상한 초과·신규 위성 매수 중지"),
            (599_600, "상한 초과·신규 위성 매수 중지"),
        )
        for satellite, expected in cases:
            with self.subTest(satellite=satellite), tempfile.TemporaryDirectory() as temporary:
                data = sample_data()
                total = 4_000_000
                data["positions"][0]["market_value_krw"] = str(total - satellite)
                data["positions"][1]["market_value_krw"] = "0"
                data["positions"][2]["market_value_krw"] = "0"
                data["positions"][3]["market_value_krw"] = str(satellite)
                report = build_markdown_report(load_scenario(write_data(temporary, data)))
                self.assertIn("코어 내부 ±5%p 점검(제안)", report)
                self.assertIn("위성 상한 점검(전체 투자자산 기준)", report)
                self.assertIn("판정 규칙 상태: 제안·미확정", report)
                self.assertIn(expected, report)

    def test_satellite_tiny_cap_excess_is_not_displayed_as_exactly_ten_percent(self) -> None:
        data = sample_data()
        data["positions"][0]["market_value_krw"] = "89999999"
        data["positions"][1]["market_value_krw"] = "0"
        data["positions"][2]["market_value_krw"] = "0"
        data["positions"][3]["market_value_krw"] = "10000001"
        data["declared_initial_assets_krw"] = "100000000"
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("실제 10.000001%", report)
            self.assertIn("정확 계산상 상한 초과", report)
            self.assertIn("상한 초과·신규 위성 매수 중지", report)
            self.assertIn("승인 없는 자동 매도 없음", report)

    def test_incomplete_investment_data_does_not_assert_satellite_cap(self) -> None:
        data = sample_data()
        data["positions"][0]["market_value_krw"] = None
        data["positions"][0]["value_status"] = "MISSING"
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("위성 상한 점검(전체 투자자산 기준): 판정 불가", report)
            self.assertNotIn("상한 도달", report)
            self.assertNotIn("상한 초과·신규 위성 매수 중지", report)

    def test_empty_manual_actual_is_missing_not_zero(self) -> None:
        data = sample_data()
        data["realm"] = "MANUAL_ACTUAL"
        data["accounts"] = []
        data["positions"] = []
        data["cashflow"] = None
        data["goal"] = None
        data["satellite"] = None
        data["assumptions"] = []
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("미입력(0원으로 간주하지 않음)", report)
            self.assertNotIn("투자자산은 0원", report)

    def test_snapshot_timestamp_must_match_report_timestamp(self) -> None:
        for invalid_timestamp in (
            "2000-01-01T00:00:00+09:00",
            "2099-01-01T00:00:00+09:00",
        ):
            data = sample_data()
            data["positions"][0]["as_of"] = invalid_timestamp
            with tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(DomainValidationError):
                    load_scenario(write_data(temporary, data))

    def test_cli_refuses_to_overwrite_its_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "same.json"
            shutil.copyfile(SAMPLE, path)
            original = path.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    run(["--input", str(path), "--output", str(path)])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(path.read_bytes(), original)

    def test_cli_refuses_hardlink_alias_of_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.json"
            alias = Path(temporary) / "different-name.md"
            shutil.copyfile(SAMPLE, source)
            try:
                os.link(source, alias)
            except OSError as exc:
                self.skipTest(f"테스트 파일시스템이 하드링크를 지원하지 않음: {exc}")
            original = source.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    run(["--input", str(source), "--output", str(alias)])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(alias.read_bytes(), original)

    def test_cli_rejects_symlink_loop_without_traceback_or_path_echo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sensitive_name = "민감-110-123-456789.md"
            output = Path(temporary) / sensitive_name
            try:
                output.symlink_to(output.name)
            except OSError as exc:
                self.skipTest(f"테스트 파일시스템이 심볼릭 링크를 지원하지 않음: {exc}")
            message = self.assert_cli_input_error_without_echo(
                ["--input", str(SAMPLE), "--output", str(output)],
                forbidden=(str(output), sensitive_name, "456789"),
            )
            self.assertIn("입력 오류", message)

    def test_cli_errors_do_not_echo_untrusted_values_or_paths(self) -> None:
        marker = "DUMMY_SECRET_MARKER_123"
        account_marker = "DUMMY-ACCOUNT-12345678"
        with tempfile.TemporaryDirectory() as temporary:
            invalid_enum = sample_data()
            invalid_enum["realm"] = marker
            enum_path = write_data(temporary, invalid_enum, "enum.json")
            enum_error = io.StringIO()
            with contextlib.redirect_stderr(enum_error):
                with self.assertRaises(SystemExit):
                    run(["--input", str(enum_path)])
            self.assertNotIn(marker, enum_error.getvalue())

            duplicate = sample_data()
            duplicate["accounts"][0]["account_id"] = account_marker
            duplicate["positions"][0]["account_id"] = account_marker
            second_account = dict(duplicate["accounts"][0])
            second_account["alias"] = "두 번째 가상 계좌"
            duplicate["accounts"].append(second_account)
            duplicate_path = write_data(temporary, duplicate, "duplicate.json")
            duplicate_error = io.StringIO()
            with contextlib.redirect_stderr(duplicate_error):
                with self.assertRaises(SystemExit):
                    run(["--input", str(duplicate_path)])
            self.assertNotIn(account_marker, duplicate_error.getvalue())

            secret_field = sample_data()
            secret_field[marker] = "value"
            secret_field_path = write_data(temporary, secret_field, "field.json")
            field_error = io.StringIO()
            with contextlib.redirect_stderr(field_error):
                with self.assertRaises(SystemExit):
                    run(["--input", str(secret_field_path)])
            self.assertNotIn(marker, field_error.getvalue())

            missing_path = Path(temporary) / marker
            path_error = io.StringIO()
            with contextlib.redirect_stderr(path_error):
                with self.assertRaises(SystemExit):
                    run(["--input", str(missing_path)])
            self.assertNotIn(marker, path_error.getvalue())

    def test_cli_argument_error_does_not_echo_account_number_like_value(self) -> None:
        sensitive = "910_876_543210"
        message = self.assert_cli_input_error_without_echo(
            ["--unknown", sensitive],
            forbidden=(sensitive, "543210"),
        )
        self.assertIn("입력 오류", message)

    def test_cli_rejects_deep_json_without_traceback_or_value_echo(self) -> None:
        marker = "DUMMY_DEEP_SECRET_MARKER"
        with tempfile.TemporaryDirectory() as temporary:
            path = write_text(
                temporary,
                "[" * 10_000 + json.dumps(marker) + "]" * 10_000,
                "deep-secret.json",
            )
            self.assert_cli_input_error_without_echo(
                ["--input", str(path)],
                forbidden=(marker, str(path)),
            )

    def test_cli_rejects_5000_digit_integer_without_traceback_or_value_echo(self) -> None:
        marker = "9" * 5_000
        raw = SAMPLE.read_text(encoding="utf-8").replace(
            '"schema_version": 1',
            '"schema_version": ' + marker,
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = write_text(temporary, raw, "long-secret-number.json")
            self.assert_cli_input_error_without_echo(
                ["--input", str(path)],
                forbidden=(marker[:100], str(path)),
            )

    def test_cli_rejects_nul_output_path_without_traceback_or_path_echo(self) -> None:
        marker = "DUMMY_NUL_PATH_MARKER"
        with tempfile.TemporaryDirectory() as temporary:
            output = str(Path(temporary) / marker) + "\x00tail"
            before = SAMPLE.read_bytes()
            self.assert_cli_input_error_without_echo(
                ["--input", str(SAMPLE), "--output", output],
                forbidden=(marker, output),
            )
            self.assertEqual(SAMPLE.read_bytes(), before)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_duplicate_root_json_key_is_rejected_without_value_echo(self) -> None:
        marker = "DUMMY_DUPLICATE_ROOT_SECRET"
        raw = SAMPLE.read_text(encoding="utf-8").replace(
            '"realm": "SAMPLE"',
            f'"realm": "{marker}",\n  "realm": "SAMPLE"',
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = write_text(temporary, raw, "duplicate-root.json")
            self.assert_cli_input_error_without_echo(
                ["--input", str(path)],
                forbidden=(marker, str(path)),
            )

    def test_duplicate_nested_json_key_is_rejected_without_value_echo(self) -> None:
        marker = "DUMMY_DUPLICATE_NESTED_SECRET"
        raw = SAMPLE.read_text(encoding="utf-8").replace(
            '"purpose": "INVESTMENT"',
            f'"purpose": "{marker}",\n      "purpose": "INVESTMENT"',
            1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = write_text(temporary, raw, "duplicate-nested.json")
            self.assert_cli_input_error_without_echo(
                ["--input", str(path)],
                forbidden=(marker, str(path)),
            )

    def test_markdown_control_characters_cannot_create_new_sections(self) -> None:
        data = sample_data()
        data["scenario_label"] = "정상\n## 6) 주문안과 이유\n- 주문 승인됨"
        data["accounts"][0]["alias"] = "가상|별칭\n## 가짜"
        data["positions"][0]["name"] = "자산|이름"
        data["assumptions"] = ["가정\n## 6) 주문안과 이유\n- 주문 승인됨"]
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertEqual(
                sum(line == "## 6) 주문안과 이유" for line in report.splitlines()),
                1,
            )
            self.assertNotIn("\n- 주문 승인됨", report)
            self.assertIn("가상\\|별칭", report)
            self.assertIn("자산\\|이름", report)

    def test_markdown_user_text_cannot_embed_external_resources(self) -> None:
        data = sample_data()
        sensitive = "110**123**456789"
        payload = (
            '<img src="https://tracking.invalid/pixel"> '
            f'![x](https://tracking.invalid/i) 계좌 {sensitive}'
        )
        data["scenario_label"] = payload
        data["accounts"][0]["alias"] = payload
        data["positions"][0]["name"] = payload
        data["assumptions"] = [payload]
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertNotIn("<img", report)
            self.assertNotIn("![x](", report)
            self.assertNotIn(sensitive, report)
            self.assertIn("&lt;img", report)
            self.assertIn(r"\!\[x\]\(", report)

    def test_cli_error_does_not_echo_sensitive_text_from_another_field(self) -> None:
        sensitive = "110-123-456789"
        data = sample_data()
        data["scenario_label"] = f"계좌 {sensitive}"
        data["realm"] = "INVALID_REALM"
        with tempfile.TemporaryDirectory() as temporary:
            path = write_data(temporary, data)
            self.assert_cli_input_error_without_echo(
                ["--input", str(path)],
                forbidden=(sensitive, str(path)),
            )

    def test_cashflow_shortfall_is_reported(self) -> None:
        data = sample_data()
        data["cashflow"]["monthly_net_income_krw"] = "800000"
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("생활비 부족: 50,000원", report)
            self.assertIn("투자·보호저축 자동 배정은 각각 0원", report)

    def test_2027_cashflow_shows_actuals_without_applying_2028_allocation(self) -> None:
        data = sample_data()
        data["cashflow"]["year"] = 2027
        data["cashflow"]["monthly_net_income_krw"] = "1000000"
        data["cashflow"]["essential_expenses_krw"] = "850000"
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn(
                "월 현금흐름(2027): 소득 1,000,000원, 필수생활비 850,000원, 잉여금 150,000원",
                report,
            )
            self.assertIn("80:20 적용 여부 미확정", report)
            self.assertIn("계획 생활비: 계산 불가/확인 필요", report)
            self.assertNotIn("투자 120,000원", report)
            self.assertIn("## 7) 위반·주의", report)

    def test_2027_zero_surplus_and_shortfall_never_create_allocations(self) -> None:
        cases = (
            ("850000", "잉여금 0원"),
            ("800000", "생활비 부족 50,000원"),
        )
        for income, expected in cases:
            with self.subTest(income=income), tempfile.TemporaryDirectory() as temporary:
                data = sample_data()
                data["cashflow"]["year"] = 2027
                data["cashflow"]["monthly_net_income_krw"] = income
                data["cashflow"]["essential_expenses_krw"] = "850000"
                report = build_markdown_report(load_scenario(write_data(temporary, data)))
                self.assertIn(expected, report)
                self.assertIn("2027년 80:20 적용 여부 미확정", report)
                self.assertNotIn("보호저축 0원", report)

    def test_out_of_range_allocation_year_does_not_abort_report(self) -> None:
        data = sample_data()
        data["allocation_year"] = 2027
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("2027년 자산배분 점검", report)
            self.assertIn("계산 불가/확인 필요", report)
            self.assertIn("## 3) 목표 대비", report)
            self.assertIn("## 7) 위반·주의", report)

    def test_satellite_fractional_boundaries_display_triggering_integer_won(self) -> None:
        data = sample_data()
        data["satellite"]["expected_annual_new_investment_krw"] = "12000005"
        data["satellite"]["measured_loss_krw"] = "600000"
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("정확 B_y: 1,200,000.5원", report)
            self.assertIn("정확 경계 600,000.25원", report)
            self.assertIn("정수 손실 최초 중단액 600,001원", report)
            self.assertIn("정확 경계 1,200,000.5원", report)
            self.assertIn("정수 손실 최초 잠금액 1,200,001원", report)
            self.assertIn("측정 손실: 600,000원 / 상태: BELOW_ALERT_THRESHOLD", report)

    def test_account_number_like_aliases_are_masked_but_normal_korean_alias_remains(self) -> None:
        aliases = (
            ("110-123-456789", "110-123-456789"),
            ("KIS 계좌 110-123-456789 별칭", "110-123-456789"),
            ("110.123.456789", "110.123.456789"),
            ("110/123/456789", "110/123/456789"),
            ("110 123 456789", "110 123 456789"),
            ("110_123_456789", "110_123_456789"),
            ("110,123,456789", "110,123,456789"),
            ("110‐123‐456789", "110‐123‐456789"),
            ("110‑123‑456789", "110‑123‑456789"),
            ("110‒123‒456789", "110‒123‒456789"),
            ("110–123–456789", "110–123–456789"),
            ("110—123—456789", "110—123—456789"),
            ("110―123―456789", "110―123―456789"),
            ("110−123−456789", "110−123−456789"),
            ("110(123)456789", "110(123)456789"),
            ("110[123]456789", "110[123]456789"),
            ("110!123!456789", "110!123!456789"),
            ("110|123|456789", "110|123|456789"),
            ("110~~123~~456789", "110~~123~~456789"),
            ("110\u200b123\u200b456789", "110\u200b123\u200b456789"),
            ("110\u2060123\u2060456789", "110\u2060123\u2060456789"),
            ("110\ufeff123\ufeff456789", "110\ufeff123\ufeff456789"),
            ("110－123－456789", "110－123－456789"),
            ("110ㅡ123ㅡ456789", "110ㅡ123ㅡ456789"),
            ("110·123·456789", "110·123·456789"),
            ("110・123・456789", "110・123・456789"),
            ("110．123．456789", "110．123．456789"),
            ("110:123:456789", "110:123:456789"),
            ("110'123'456789", "110'123'456789"),
            ("110#123#456789", "110#123#456789"),
            ("１１０－１２３－４５６７８９", "１１０－１２３－４５６７８９"),
            ("2028-01-01 개설 ISA", "2028-01-01 개설 ISA"),
        )
        for alias, sensitive in aliases:
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as temporary:
                data = sample_data()
                data["accounts"][0]["alias"] = alias
                report = build_markdown_report(load_scenario(write_data(temporary, data)))
                self.assertNotIn(sensitive, report)
                self.assertIn("계좌번호 형태 마스킹", report)
                if "456789" in alias:
                    self.assertNotIn("456789", report)

        with tempfile.TemporaryDirectory() as temporary:
            data = sample_data()
            data["accounts"][0]["alias"] = "장기투자 계좌"
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("장기투자 계좌", report)

    def test_cli_report_masks_account_number_like_text_in_every_visible_text_field(self) -> None:
        sensitive = "110-123-456789"
        data = sample_data()
        data["scenario_label"] = f"보고서 제목 계좌 {sensitive}"
        data["accounts"][0]["alias"] = f"별칭 계좌 {sensitive}"
        data["positions"][0]["name"] = f"종목 이름 계좌 {sensitive}"
        data["assumptions"] = [f"가정 문구 계좌 {sensitive}"]

        with tempfile.TemporaryDirectory() as temporary:
            input_path = write_data(temporary, data)
            output_path = Path(temporary) / "report.md"
            self.assertEqual(
                run(["--input", str(input_path), "--output", str(output_path)]),
                0,
            )
            report = output_path.read_text(encoding="utf-8")

        self.assertNotIn(sensitive, report)
        self.assertNotIn("456789", report)
        self.assertGreaterEqual(report.count("계좌번호 형태 마스킹"), 4)
        for ordinary_text in ("보고서 제목", "별칭 계좌", "종목 이름", "가정 문구"):
            self.assertIn(ordinary_text, report)

    def test_r3_compatibility_variants_are_masked_in_every_visible_text_field(self) -> None:
        ascii_digit_variants = (
            "110－123－456789",
            "110ㅡ123ㅡ456789",
            "110·123·456789",
            "110・123・456789",
            "110．123．456789",
            "110:123:456789",
            "110'123'456789",
            "110#123#456789",
        )
        fullwidth_table = str.maketrans("0123456789", "０１２３４５６７８９")
        variants = (
            *ascii_digit_variants,
            *(value.translate(fullwidth_table) for value in ascii_digit_variants),
            "１１０-１２３-４５６７８９",
            "110ㅡ１２３·456789",
        )
        self.assertEqual(len(variants), 18)
        for sensitive in variants:
            with self.subTest(sensitive=sensitive), tempfile.TemporaryDirectory() as temporary:
                data = sample_data()
                data["scenario_label"] = f"보고서 제목 {sensitive} 끝"
                data["accounts"][0]["alias"] = f"계좌 별칭 {sensitive} 끝"
                data["positions"][0]["name"] = f"종목 이름 {sensitive} 끝"
                data["assumptions"] = [f"가정 문구 {sensitive} 끝"]
                report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertNotIn(sensitive, report)
            self.assertNotIn(unicodedata.normalize("NFKC", sensitive), report)
            self.assertNotIn("456789", report)
            self.assertGreaterEqual(report.count("계좌번호 형태 마스킹"), 4)

    def test_visible_text_sanitizing_preserves_normal_dates_amounts_and_names(self) -> None:
        data = sample_data()
        data["scenario_label"] = "ＡＢＣ ①호 2028년 장기 자산 보고서"
        data["accounts"][0]["alias"] = "장기투자 계좌"
        data["positions"][0]["name"] = "KODEX 200 정상 종목"
        data["assumptions"] = [
            "기준일 2028년 1월 1일, 생활비 850,000원, 연 6% 총수익률 가정"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
        for ordinary_text in (
            "ＡＢＣ ①호 2028년 장기 자산 보고서",
            "장기투자 계좌",
            "KODEX 200 정상 종목",
            "기준일 2028년 1월 1일, 생활비 850,000원, 연 6% 총수익률 가정",
            "2028-01-01T00:00:00+09:00",
            "다음 해 예정 투자금 12,000,000원",
        ):
            self.assertIn(ordinary_text, report)

    def test_ambiguous_date_and_amount_like_free_text_is_masked(self) -> None:
        date_like = "2028-01-01"
        amount_like = "110,123,456,789원"
        data = sample_data()
        data["scenario_label"] = f"보고서 계좌 {date_like}"
        data["positions"][0]["name"] = f"종목 계좌 {amount_like}"
        data["assumptions"] = [f"가정 계좌 {date_like}"]
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
        self.assertNotIn(f"계좌 {date_like}", report)
        self.assertNotIn(amount_like, report)
        self.assertNotIn("456789", report)
        self.assertGreaterEqual(report.count("계좌번호 형태 마스킹"), 3)

    def test_manual_actual_uses_manual_data_warnings(self) -> None:
        data = sample_data()
        data["realm"] = "MANUAL_ACTUAL"
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("수동 실제자료는 외부 계좌와 자동 대조되지 않았습니다", report)
            self.assertIn("- 입력 가정:", report)
            self.assertNotIn("샘플 상세액은 실제 잔고가 아닙니다", report)

    def test_2062_unknown_allocation_does_not_abort_other_report_sections(self) -> None:
        data = sample_data()
        data["allocation_year"] = 2062
        with tempfile.TemporaryDirectory() as temporary:
            report = build_markdown_report(load_scenario(write_data(temporary, data)))
            self.assertIn("2062~2064", report)
            self.assertIn("## 3) 목표 대비", report)
            self.assertIn("## 7) 위반·주의", report)

    def test_json_float_and_scientific_krw_text_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            floating = sample_data()
            floating["declared_initial_assets_krw"] = 4_000_000.0
            with self.assertRaises(DomainValidationError):
                load_scenario(write_data(temporary, floating, "float.json"))

            scientific = sample_data()
            scientific["declared_initial_assets_krw"] = "4e6"
            with self.assertRaises(DomainValidationError):
                load_scenario(write_data(temporary, scientific, "scientific.json"))

    def test_cli_handles_korean_and_space_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "한글 경로" / "샘플 보고서.md"
            result = run(["--input", str(SAMPLE), "--output", str(output)])
            self.assertEqual(result, 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("OFFLINE / 가상 샘플", text)

    def test_network_clients_are_confined_to_read_only_provider_modules(self) -> None:
        network_markers = (
            "import requests",
            "import urllib",
            "import socket",
            "import httpx",
            "from requests",
            "from urllib",
            "from socket",
            "from httpx",
        )
        # Each allowlisted module is read-only.  ``market_information`` only
        # reads the fixed official RSS/Atom registry and has no broker/order
        # adapter; the forbidden-order scan below remains unchanged.
        approved = {"providers.py", "kr_providers.py", "market_information.py"}
        for path in (ROOT / "asset_assistant").glob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            used = [marker for marker in network_markers if marker in source]
            if used:
                self.assertIn(path.name, approved, f"{path.name}: {used}")

        combined = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in (ROOT / "asset_assistant").glob("*.py")
        )
        for forbidden_order_endpoint in (
            "order-cash",
            "order-rvsecncl",
            "/orders",
            "submit_order",
            "place_order",
        ):
            self.assertNotIn(forbidden_order_endpoint, combined)


if __name__ == "__main__":
    unittest.main()
