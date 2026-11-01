# -*- coding: utf-8 -*-
# pylint: disable=missing-docstring
# pylint: disable=attribute-defined-outside-init
# pylint: disable=protected-access
import datetime
import re
from importlib.metadata import version
from pathlib import Path, PosixPath
from types import SimpleNamespace
from unittest import mock

import pytest
import yaml
from beancount.core import data, amount
from beancount.core.number import D
from beancount.parser import printer
from click.testing import CliRunner

from g2b.g2b import main, GnuCash2Beancount, G2BException


class TestCLI:

    def setup_method(self):
        self.cli_runner = CliRunner()

    @mock.patch("g2b.g2b.GnuCash2Beancount.write_beancount_file")
    def test_cli_calls_write_beancount_file(self, mock_write_beancount_file, tmp_path):
        gnucash_path = tmp_path / "book.gnucash"
        gnucash_path.touch()
        config_path = tmp_path / "config.yaml"
        test_config = {"converter": {"loglevel": "INFO"}}
        config_path.write_text(yaml.dump(test_config))
        command = f"-i {gnucash_path} -o book.beancount -c {config_path}"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 0, f"{result.exc_info}"
        mock_write_beancount_file.assert_called()

    def test_cli_raises_on_non_existing_input_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        test_config = {"converter": {"loglevel": "INFO"}}
        config_path.write_text(yaml.dump(test_config))
        command = f"-i test.gnucash -o book.beancount -c {config_path}"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 2, f"{result.exc_info}"
        assert re.match(r".*Path 'test.gnucash' does not exist.*", result.output, flags=re.DOTALL)

    def test_cli_raises_on_non_existing_config_file(self, tmp_path):
        gnucash_path = tmp_path / "book.gnucash"
        gnucash_path.touch()
        command = f"-i {gnucash_path} -o book.beancount -c test_config.yml"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 2, f"{result.exc_info}"
        assert re.match(
            r".*Path 'test_config.yml' does not exist.*", result.output, flags=re.DOTALL
        )

    def test_cli_version(self):
        result = self.cli_runner.invoke(main, "--version")
        assert result.exit_code == 0, f"{result.exc_info}"
        assert version("g2b") in result.output


class GnuCash2BeancountTestBase:

    def setup_method(self):
        self.test_config = {
            "converter": {"loglevel": "INFO"},
            "gnucash": {
                "default_currency": "EUR",
                "thousands_symbol": ".",
                "decimal_symbol": ",",
                "reconciled_symbol": "b",
                "not_reconciled_symbol": "n",
                "account_rename_patterns": [
                    ["Assets:Bank:Some Bank \\(test\\)", "Assets:Bank:Some Test Bank"],
                    ["Assets:Bank:Some USD Bank ", "Assets:Bank:Some Bank (USD)"],
                    ["Expenses:Groceries", "Expenses:MyGroceries"],
                ],
                "non_default_account_currencies": {"Assets:Current-Assets:Wallet-Nzd": "NZD"},
            },
            "beancount": {
                "options": [["operating_currency", "EUR"], ["title", "Exported GnuCash Book"]],
                "plugins": ["beancount.plugins.auto"],
            },
        }
        self.gnucash_path = Path("tests/test_book.gnucash")

    def _create_converter(self, tmp_path, gnucash_config=None, filepath=Path()):
        if gnucash_config:
            self.test_config["gnucash"].update(gnucash_config)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        return GnuCash2Beancount(filepath, Path(), config_path)


class TestGnuCash2Beancount(GnuCash2BeancountTestBase):

    def test_configs_returns_valid_yaml(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert isinstance(g2b._configs, dict)

    def test_configs_raises_on_invalid_yaml(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("some : invalid : yaml")
        with pytest.raises(G2BException, match="Error while parsing config file"):
            _ = GnuCash2Beancount(Path(), Path(), config_path)

    def test_bean_config_adds_switched_to_beancount_event_if_no_events_are_configured(
        self, tmp_path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert g2b._bean_config.get("events") == {
            datetime.date.today(): "misc Changed from GnuCash to Beancount"
        }

    def test_bean_config_adds_switched_to_beancount_event_if_events_are_configured(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        self.test_config["beancount"]["events"] = {datetime.date.today(): "test Test Event"}
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert g2b._bean_config.get("events") == {
            datetime.date.today(): "test Test Event",
            datetime.date.today(): "misc Changed from GnuCash to Beancount",
        }

    def test_converter_config_returns_only_converter_configurations(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert isinstance(g2b._converter_config, dict)
        assert g2b._converter_config == self.test_config.get("converter")

    def test_account_rename_patterns_enriches_config_with_default_patterns(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        for pattern in GnuCash2Beancount._DEFAULT_ACCOUNT_RENAME_PATTERNS:
            assert pattern in g2b._account_rename_patterns

    @pytest.mark.parametrize(
        "account_name, expected_name",
        [
            ("Assets:Bank:Some Bank (test)", "Assets:Bank:Some-Test-Bank"),
            ("Liabilities:Credit Card (test)", "Liabilities:Credit-Card-test"),
            ("Equity:Opening Balances (test)", "Equity:Opening-Balances-test"),
            ("Assets:Some Wrong 23 ! Charts in name", "Assets:Some-Wrong-23-Charts-in-name"),
            ("Assets:Account@Symbol", "Assets:Account-Symbol"),
            ("Expenses:Caf\u00e9&Restaurant", "Expenses:Café-Restaurant"),
            ("Income:Salary#Bonus", "Income:Salary-Bonus"),
            ("Liabilities:Loan$Payment", "Liabilities:Loan-Payment"),
            ("Assets:Bank%Account", "Assets:Bank-Account"),
            ("Expenses:Travel^Expenses", "Expenses:Travel-Expenses"),
            ("Assets:Account*With*Stars", "Assets:Account-With-Stars"),
            ("Income:Dividends+Interest", "Income:Dividends-Interest"),
            ("Assets:Account=Test", "Assets:Account-Test"),
            ("Expenses:Shop[Retail]", "Expenses:Shop-Retail"),
            ("Assets:Account{Curly}", "Assets:Account-Curly"),
            ("Liabilities:Debt|Payment", "Liabilities:Debt-Payment"),
            ("Assets:Account\\Backslash", "Assets:Account-Backslash"),
            ("Expenses:Food;Drinks", "Expenses:Food-Drinks"),
            ("Income:Rent'Payment", "Income:Rent-Payment"),
            ('Assets:Account"Quotes"', "Assets:Account-Quotes"),
            ("Expenses:Items<50", "Expenses:Items-50"),
            ("Expenses:Items_50", "Expenses:Items-50"),
            ("Assets:Account>Symbol", "Assets:Account-Symbol"),
            ("Income:Revenue?Other", "Income:Revenue-Other"),
            ("Assets:Account/Slash", "Assets:Account-Slash"),
            ("Expenses:M\u00fcller-Schmidt", "Expenses:Müller-Schmidt"),
            ("Assets:Some Bank (USD)", "Assets:Some-Bank-USD"),
            ("Assets:Some Bank (áéíóúöüőű)", "Assets:Some-Bank-áéíóúöüőű"),
            ("Assets:Some Bank (ÁÉÍÓÚÖÜŐŰ)", "Assets:Some-Bank-ÁÉÍÓÚÖÜŐŰ"),
        ],
    )
    def test_apply_renaming_patterns_return_valid_beancount_names(
        self, tmp_path, account_name, expected_name
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        corrected_name = g2b._apply_renaming_patterns(account_name)
        assert corrected_name == expected_name

    def test_write_beancount_file_writes_a_valid_beancount_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        output_path = tmp_path / "bean.beancount"
        g2b = GnuCash2Beancount(self.gnucash_path, output_path, config_path)
        g2b.write_beancount_file()
        with open(output_path, "r", encoding="utf8") as beanfile:
            content = beanfile.read()
        example_transaction = """2024-05-06 ! "Transfer"
  ! Assets:Current-Assets:CheckingAccount-Foo-Bank   1000.0 EUR
  ! Assets:Current-Assets:Checking-Account          -1000.0 EUR
"""
        assert example_transaction in content

    def test_get_open_account_directives_creates_beancount_open_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        open_directives = g2b._get_open_account_directives(g2b._get_transactions())
        expected_account_openings = [
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 1).date(),
                account="Assets:Current-Assets:Checking-Account",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 1).date(),
                account="Equity:Opening-Balances",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 3).date(),
                account="Expenses:MyGroceries",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 6).date(),
                account="Assets:Current-Assets:CheckingAccount-Foo-Bank",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 9).date(),
                account="Assets:Current-Assets:Wallet-NZD",
                currencies=["NZD"],
                booking=None,
            ),
        ]
        assert open_directives == expected_account_openings

    def test_get_transaction_directives_creates_beancount_transaction_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        transactions = g2b._get_transactions()
        expected_transactions = [
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 1).date(),
                flag="!",
                payee="",
                narration="Opening",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("10000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Equity:Opening-Balances",
                        units=amount.Amount(D("-10000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 3).date(),
                flag="!",
                payee="",
                narration="Groceries",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Expenses:MyGroceries",
                        units=amount.Amount(D("120.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("-120.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 6).date(),
                flag="!",
                payee="",
                narration="Transfer",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:CheckingAccount-Foo-Bank",
                        units=amount.Amount(D("1000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("-1000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 9).date(),
                flag="!",
                payee="",
                narration="MoneyTransfer",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Wallet-NZD",
                        units=amount.Amount(D("50.0"), currency="NZD"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(
                            D("-27.950"),
                            currency="EUR",
                        ),
                        cost=None,
                        price=amount.Amount(
                            D("1.788908765652951699463327370"),
                            currency="NZD",
                        ),
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
        ]
        assert transactions == expected_transactions

    def test_get_transactions_exports_gnucash_num_as_beancount_link(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        for transaction in g2b._book.transactions:
            if transaction.description == "Transfer":
                transaction.num = "REF 2024/001"
                transaction.splits[0].account.type = "PAYABLE"
        transactions = g2b._get_transactions()
        transfer = next(txn for txn in transactions if txn.narration == "Transfer")
        assert transfer.links == {"bill-REF-2024/001"}
        assert transfer.meta["bill_id"] == "REF 2024/001"

    def test_get_document_type_detects_invoices_and_bills_from_accounts(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        invoice = SimpleNamespace(
            splits=[SimpleNamespace(account=SimpleNamespace(type="RECEIVABLE"))]
        )
        bill = SimpleNamespace(splits=[SimpleNamespace(account=SimpleNamespace(type="PAYABLE"))])
        ordinary = SimpleNamespace(splits=[SimpleNamespace(account=SimpleNamespace(type="BANK"))])
        assert g2b._get_document_type(invoice) == "invoice"
        assert g2b._get_document_type(bill) == "bill"
        assert g2b._get_document_type(ordinary) is None

    def test_get_header_str_contains_options_and_plugins(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        header = g2b._get_header_str()
        expected_header = """plugin "beancount.plugins.auto"

option "operating_currency" "EUR"
option "title" "Exported GnuCash Book"

"""
        assert header == expected_header

    def test_get_commodities_contains_default_and_non_default_currencies(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        g2b._get_transactions()
        commodities = g2b._get_commodities()
        commodities_str = "\n".join([printer.format_entry(com) for com in commodities])
        expected_commodities = "2024-05-01 commodity EUR\n\n2024-05-09 commodity NZD\n"
        assert commodities_str == expected_commodities

    @mock.patch("g2b.g2b.parse_file")
    def test_verify_output_calls_beancount_parse_file(self, mock_parse, tmp_path):
        mock_parse.return_value = [[], [], {}]
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._verify_output()
        mock_parse.assert_called()

    @mock.patch("g2b.g2b.parse_file", mock.MagicMock(return_value=[[], [], {}]))
    @mock.patch("g2b.g2b.validate")
    def test_verify_output_calls_beancount_validate(self, mock_validate, tmp_path):
        mock_validate.return_value = {}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._verify_output()
        mock_validate.assert_called()

    def test_events_created_beancount_event_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        events = g2b._get_event_directives()
        assert events == [
            data.Event(
                date=datetime.date.today(),
                type="misc",
                description="Changed from GnuCash to Beancount",
                meta={},
            )
        ]

    def test_read_book_raises_on_wrong_file_format(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_file = tmp_path / "book.gnucash"
        gnucash_file.write_text("wrong format")
        g2b = GnuCash2Beancount(gnucash_file, Path(), config_path)
        with pytest.raises(G2BException, match="File does not exist or wrong format exception.*"):
            g2b._read_gnucash_book()

    def test_commodity_has_precision_if_fava_config_is_present(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        self.test_config.update({"fava": {"commodity-precision": "3"}})
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        g2b._get_transactions()
        commodities = g2b._get_commodities()
        for commodity in commodities:
            assert commodity.meta.get("precision") == "3"

    def test_commodity_has_no_precision_if_fava_config_is_not_present(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        g2b._get_transactions()
        commodities = g2b._get_commodities()
        assert g2b._fava_config == {}
        for commodity in commodities:
            assert "precision" not in commodity.meta

    def test_postings_have_no_flag_if__flag_postings__is_set_to_false(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        self.test_config.update({"beancount": {"flag_postings": False}})
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        transactions = g2b._get_transactions()
        for transaction in transactions:
            assert transaction.flag == "*"
            for posting in transaction.postings:
                assert posting.flag is None

    def test_get_prices_returns_beancount_prices(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        prices = g2b._get_prices()
        for price in prices:
            assert isinstance(price, data.Price)

    @mock.patch("beancount.core.amount.Amount", mock.MagicMock())
    @mock.patch("g2b.g2b.GnuCash2Beancount._calculate_price_of_split", mock.MagicMock())
    @mock.patch("g2b.g2b.GnuCash2Beancount._apply_renaming_patterns", mock.MagicMock())
    def test_get_postings_includes_meta_information(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        mock_splits = [
            mock.MagicMock(memo=" Test Memo ", action=" Test Action "),
            mock.MagicMock(memo="Test Memo", action=None),
            mock.MagicMock(memo=None, action="Test Action"),
        ]
        postings = g2b._get_postings(mock_splits)
        assert len(postings) == 3
        assert postings[0].meta == {"action": "Test Action", "memo": "Test Memo"}
        assert postings[1].meta == {"memo": "Test Memo"}
        assert postings[2].meta == {"action": "Test Action"}


class TestSlots(GnuCash2BeancountTestBase):

    def test_slots_config_defaults_to_assoc_uri(self, tmp_path):
        g2b = self._create_converter(tmp_path)
        assert g2b._slots_config == ["assoc_uri"]

    def test_slots_config_returns_configured_slots(self, tmp_path):
        g2b = self._create_converter(tmp_path, {"slots": ["date-posted", "assoc_uri"]})
        assert g2b._slots_config == ["date-posted", "assoc_uri"]

    def test_slot_rename_patterns_are_empty_by_default(self, tmp_path):
        g2b = self._create_converter(tmp_path)
        assert g2b._slot_rename_patterns == {}

    @pytest.mark.parametrize(
        "configured_patterns",
        [
            [["date-posted", "posted"]],
            {"date-posted": "posted"},
        ],
    )
    def test_slot_rename_patterns_returns_a_mapping(self, tmp_path, configured_patterns):
        g2b = self._create_converter(tmp_path, {"slots_rename_patterns": configured_patterns})
        assert g2b._slot_rename_patterns == {"date-posted": "posted"}

    def test_get_slot_metadata_returns_empty_dict_without_slots(self, tmp_path):
        g2b = self._create_converter(tmp_path)
        assert not g2b._get_slot_metadata(None)
        assert not g2b._get_slot_metadata([])

    def test_get_slot_metadata_ignores_slots_that_are_not_configured(self, tmp_path):
        g2b = self._create_converter(tmp_path)
        slots = [SimpleNamespace(name="date-posted", value=datetime.date(2024, 5, 1))]
        assert not g2b._get_slot_metadata(slots)

    def test_get_slot_metadata_exports_configured_slots(self, tmp_path):
        g2b = self._create_converter(tmp_path, {"slots": ["date-posted"]})
        slots = [SimpleNamespace(name="date-posted", value=datetime.date(2024, 5, 1))]
        assert g2b._get_slot_metadata(slots) == {"date-posted": "2024-05-01"}

    def test_get_slot_metadata_exports_assoc_uri_by_default(self, tmp_path):
        g2b = self._create_converter(tmp_path)
        slots = [SimpleNamespace(name="assoc_uri", value="https://example.com/receipt.pdf")]
        assert g2b._get_slot_metadata(slots) == {"url": "https://example.com/receipt.pdf"}

    @pytest.mark.parametrize(
        "url",
        ["http://example.com/receipt.pdf", "https://example.com/receipt.pdf"],
    )
    def test_get_slot_metadata_maps_url_assoc_uri_to_url(self, tmp_path, url):
        g2b = self._create_converter(tmp_path)
        slots = [SimpleNamespace(name="assoc_uri", value=f"  {url}  ")]
        assert g2b._get_slot_metadata(slots) == {"url": url}

    def test_get_slot_metadata_maps_non_url_assoc_uri_to_document(self, tmp_path):
        g2b = self._create_converter(tmp_path)
        slots = [SimpleNamespace(name="assoc_uri", value=" receipts/receipt.pdf ")]
        assert g2b._get_slot_metadata(slots) == {"document": "receipts/receipt.pdf"}

    def test_get_slot_metadata_renames_slots(self, tmp_path):
        g2b = self._create_converter(
            tmp_path,
            {
                "slots": ["date-posted"],
                "slots_rename_patterns": [["date-posted", "posted"]],
            },
        )
        slots = [SimpleNamespace(name="date-posted", value=datetime.date(2024, 5, 1))]
        assert g2b._get_slot_metadata(slots) == {"posted": "2024-05-01"}

    def test_get_slot_metadata_skips_empty_slots(self, tmp_path):
        g2b = self._create_converter(tmp_path, {"slots": ["date-posted", "notes"]})
        slots = [
            None,
            SimpleNamespace(name=None, value="ignored"),
            SimpleNamespace(name="date-posted", value=None),
            SimpleNamespace(name="date-posted", value="   "),
            SimpleNamespace(name="notes", value=0),
        ]
        assert g2b._get_slot_metadata(slots) == {"notes": "0"}

    def test_get_transactions_includes_metadata_of_configured_slots(self, tmp_path):
        g2b = self._create_converter(
            tmp_path,
            {
                "slots": ["date-posted"],
                "slots_rename_patterns": [["date-posted", "posted"]],
            },
            filepath=self.gnucash_path,
        )
        g2b._read_gnucash_book()
        transactions = g2b._get_transactions()
        assert [transaction.meta["posted"] for transaction in transactions] == [
            "2024-05-01",
            "2024-05-03",
            "2024-05-06",
            "2024-05-09",
        ]
        for transaction in transactions:
            assert transaction.meta["filename"] == self.gnucash_path
            assert transaction.meta["lineno"] == -1
