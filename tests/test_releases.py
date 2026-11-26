"""Press-release extraction: exhibit choice, headline, backlog sentences, and no contact details in stored text."""
from indintel.sources import sec_releases as sr


def test_pick_exhibit_naming_styles():
    prim = "form8-k.htm"
    assert sr.pick_exhibit([prim, "cat_exx991x1qx2019xearning.htm", "ex992toformxretailstatisti.htm"], prim) \
        == "cat_exx991x1qx2019xearning.htm"
    assert sr.pick_exhibit([prim, "cmi2018q48-kex99.htm"], prim) == "cmi2018q48-kex99.htm"
    assert sr.pick_exhibit([prim, "lrcx_exhibitx991xq4x2019.htm"], prim) == "lrcx_exhibitx991xq4x2019.htm"
    assert sr.pick_exhibit([prim, "q42018nvtpressrelease.htm"], prim) == "q42018nvtpressrelease.htm"
    assert sr.pick_exhibit([prim, "lettertoshareholderdated.htm", "pressreleasedatedasofmay.htm"], prim) \
        == "pressreleasedatedasofmay.htm"
    assert sr.pick_exhibit([prim, "tm2412345d1_ex10-1.htm", "R1.htm", "0000-index.html"], prim) is None   # 8-K only
    assert sr.pick_exhibit([prim, "tm2412345d1_ex99-1.htm", "tm2412345d1_ex10-1.htm"], prim) == "tm2412345d1_ex99-1.htm"


def test_paragraphs_join_inline_tags_and_line_breaks():
    raw = ("<html><body><p><font size=4>Q</font><font size=3>UANTA</font> <font size=4>S</font><font size=3>ERVICES"
           "</font> REPORTS<br>RESULTS</p>\n<p>Second\nparagraph &amp; more</p></body></html>")
    assert sr.paragraphs(raw) == ["QUANTA SERVICES REPORTS RESULTS", "Second paragraph & more"]
    assert sr.paragraphs(raw, lines=True) == ["QUANTA SERVICES REPORTS", "RESULTS", "Second paragraph & more"]


def test_extract_release_laid_out_with_line_breaks_and_slide_deck():
    release = ("<p>Exhibit 99.1<br>Example Inc.<br>4Q 2018 Earnings Release<br>January 28, 2019<br>FOR IMMEDIATE RELEASE"
               "<br>Media Contact: Jane Example<br>(555) 010-1234<br>Example Reports Fourth-Quarter and Full-Year 2018 "
               "Results; Provides Outlook for 2019</p><p>SPRINGFIELD, Ill. — Example Inc. (NYSE: EXM) reported sales of "
               "$14.3 billion. The order backlog was $16.5 billion, up 10% from a year earlier.</p>")
    head, kpi, is_release = sr.extract(release, "Example Inc")
    assert is_release
    assert head == "Example Reports Fourth-Quarter and Full-Year 2018 Results; Provides Outlook for 2019"
    assert kpi == ["The order backlog was $16.5 billion, up 10% from a year earlier."]
    slides = "<p>Example at a Glance (NASDAQ: EXM) Founded 1976 Headquarters $8.2B Revenue</p><p>Growth Strategy</p>"
    assert sr.extract(slides, "Example Inc") == ("", [], False)


def test_headline_skips_contacts_addresses_cover_and_dates():
    paras = ["SECURITIES AND EXCHANGE COMMISSION", "NEWS RELEASE", "Contacts:", "Jane Example (555) 010-1234",
             "Jane Example, CFA - Investor Relations", "800 S. Example Road, 12 th Floor", "Coral Gables, Florida 33134",
             "Date: July 30, 2020 NEWS RELEASE", "Example Corp Announces Third Quarter 2020", "Results and Raises Guidance",
             "SPRINGFIELD, Ill., July 30, 2020 — Example Corp (NYSE: EXM) today reported third-quarter sales of $1.2 "
             "billion. Backlog was $3.4 billion, up 12% from a year earlier."]
    assert sr.headline(paras, "Example Corp") == "Example Corp Announces Third Quarter 2020 Results and Raises Guidance"
    assert sr.kpi_sentences(paras) == ["Backlog was $3.4 billion, up 12% from a year earlier."]
    assert sr.lead_sentence(paras).startswith("Example Corp (NYSE: EXM) today reported")


def test_headline_edge_cases():
    # a headline about naming an investor-relations officer is news, not a contact block; 'and ...' continues it
    paras = ["Jon Example", "Director, External Communications", "For Immediate Release", "January 13, 2022",
             "Example Inc. names Chris Example, Vice President, Investor Relations",
             "and Luther Example, Vice President, Corporate Controller",
             "Columbus, Indiana – Today, Example Inc. (NYSE: EXM) announced that it has named two officers."]
    assert sr.headline(paras, "Example Inc") == ("Example Inc. names Chris Example, Vice President, Investor Relations "
                                                 "and Luther Example, Vice President, Corporate Controller")
    # a company renamed since (Modine -> Modexus): the alias still identifies its headline
    paras = ["NEWS RELEASE", "Modine Exploring Strategic Alternatives for Automotive Business",
             "Racine, WI – January 29 – Modine Manufacturing Company (NYSE: MOD) today announced a review."]
    assert sr.headline(paras, "Modexus Solutions, Inc.", "MOD") == \
        "Modine Exploring Strategic Alternatives for Automotive Business"
    # a long title-case headline broken over two blocks
    paras = ["MasTec Announces Fourth Quarter and Annual 2024 Financial",
             "Results With Record Backlog and Provides Initial 2025 Guidance", "•",
             "CORAL GABLES, FL (February 27, 2025) — MasTec, Inc. (NYSE: MTZ) today announced results."]
    assert sr.headline(paras, "MasTec, Inc.") == ("MasTec Announces Fourth Quarter and Annual 2024 Financial Results "
                                                  "With Record Backlog and Provides Initial 2025 Guidance")
    # double dateline, no headline line in the document
    assert sr.strip_dateline("FREMONT, Calif. -- (PRNewswire) – November 5, 2026 — Lam Research Corporation said.") \
        == "Lam Research Corporation said."


def test_sentence_split_keeps_abbreviations_and_initials():
    s = "Example Inc. (NYSE: EXM) said Blake D. Moret of the U.S. unit joined. Orders rose 5%. “Strong,” said the CFO. Next."
    assert sr.split_sentences(s) == ["Example Inc. (NYSE: EXM) said Blake D. Moret of the U.S. unit joined.",
                                     "Orders rose 5%.", "“Strong,” said the CFO.", "Next."]


def test_scrub_removes_phone_numbers_and_emails():
    assert sr.scrub("Global Corporate Affairs 832-573-0958 someone@example.com") == "Global Corporate Affairs"
    assert sr.scrub("Jane Example (408) 986-7977") == "Jane Example"
    assert sr.scrub("Backlog of $14.6 billion, up 3%.") == "Backlog of $14.6 billion, up 3%."


def test_form_lead_for_8k_without_press_release():
    paras = ["UNITED STATES SECURITIES AND EXCHANGE COMMISSION", "FORM 8-K",
             "Item 1.01. Entry into a Material Definitive Agreement.",
             "On September 10, 2019, Example Inc. entered into a credit agreement with a group of banks. More text."]
    assert sr.form_lead(paras, ["1.01"]) == \
        "On September 10, 2019, Example Inc. entered into a credit agreement with a group of banks."
