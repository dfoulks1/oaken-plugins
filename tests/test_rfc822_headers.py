# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import ValidationError

from oaken_plugins.formatter_rfc822 import Rfc822Config, Rfc822Formatter
from oaken_plugins.formatter_rfc822.headers import auth_results, parse_header, tags

DKIM = "v=1; a=rsa-sha256; d=apache.org; s=mail;\r\n\th=from:to; b=abc\r\n def="
RESULTS = ("mx.google.com;\r\n dkim=pass header.i=@apache.org header.s=mail;\r\n spf=pass (google.com: domain of x designates 1.2.3.4 as permitted sender) smtp.mailfrom=x@apache.org;\r\n"
           " dmarc=fail (p=NONE) header.from=apache.org")
RAW = f"DKIM-Signature: {DKIM}\r\nAuthentication-Results: {RESULTS}\r\nFrom: a@b.example\r\nSubject: hi\r\n\r\nhello\r\n"


def test_a_dkim_signature_splits_into_its_tags_with_folded_values_joined() -> None:
    assert tags(DKIM) == {"v": "1", "a": "rsa-sha256", "d": "apache.org", "s": "mail", "h": "from:to", "b": "abcdef="}


def test_text_that_is_not_tag_equals_value_is_not_split() -> None:
    assert tags("just some words") is None and tags("") is None
    assert parse_header("x-mailer", ["Thunderbird 115"]) == [{"value": "Thunderbird 115"}]


def test_authentication_results_gives_the_server_and_each_methods_result_comments_dropped() -> None:
    assert auth_results(RESULTS) == {"authserv-id": "mx.google.com", "results": {"dkim": "pass", "spf": "pass", "dmarc": "fail"}}
    assert auth_results("mx.example; spf=pass; spf=fail")["results"] == {"spf": "pass"}  # the first of a repeated method wins


def test_a_repeated_header_gives_one_entry_per_copy() -> None:
    assert [t["d"] for t in parse_header("dkim-signature", ["d=a.org; s=x", "d=b.org; s=y"])] == ["a.org", "b.org"]


async def test_the_formatter_adds_a_part_for_each_header_asked_for_and_skips_one_the_message_lacks() -> None:
    f = Rfc822Formatter()
    f.config = Rfc822Config(parse_headers=["DKIM-Signature", "authentication-results", "x-absent"])
    parts = await f.split(RAW)
    assert parts["dkim-signature"].content[0]["d"] == "apache.org"
    assert parts["authentication-results"].content[0]["results"]["dmarc"] == "fail"
    assert "x-absent" not in parts and parts["headers"].content["subject"] == ["hi"]  # the raw headers part is untouched


async def test_without_the_setting_the_parts_are_as_before() -> None:
    assert sorted(await Rfc822Formatter().split(RAW)) == ["clean", "headers", "text"]


@pytest.mark.parametrize("bad", [["text"], ["clean"], ["attachment-1"], ["two words"], ["x;y"]])
def test_a_name_that_is_not_a_header_or_that_takes_a_reserved_part_is_refused(bad: list[str]) -> None:
    with pytest.raises(ValidationError):
        Rfc822Config(parse_headers=bad)
