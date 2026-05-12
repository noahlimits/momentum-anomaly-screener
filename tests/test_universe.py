from src.universe import _source_date_from_companiesmarketcap, format_ishares_ticker


def test_format_ishares_ticker_maps_common_exchanges_to_yfinance_suffixes():
    assert format_ishares_ticker("700", "Hong Kong Exchanges And Clearing Ltd", "China") == "0700.HK"
    assert format_ishares_ticker("7203", "Tokyo Stock Exchange", "Japan") == "7203.T"
    assert format_ishares_ticker("005930", "Korea Exchange (Stock Market)", "Korea (South)") == "005930.KS"
    assert format_ishares_ticker("ASML", "Euronext Amsterdam", "Netherlands") == "ASML.AS"
    assert format_ishares_ticker("HSBA", "London Stock Exchange", "United Kingdom") == "HSBA.L"


def test_format_ishares_ticker_leaves_us_tickers_plain():
    assert format_ishares_ticker("NVDA", "NASDAQ", "United States") == "NVDA"
    assert format_ishares_ticker("BRK.B", "NYSE", "United States") == "BRK-B"


def test_source_date_from_companiesmarketcap_extracts_holdings_date():
    html = 'Etf holdings as of <span class="background-ya">March 31, 2026</span>'
    assert _source_date_from_companiesmarketcap(html) == "March 31, 2026"
