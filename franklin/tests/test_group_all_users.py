"""Tests for franklin_radicale.group_all_users and the app patch it relies on."""
import os
import re
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))          # franklin/
sys.path.insert(0, os.path.join(HERE, "..", ".."))    # repo root (radicale)

from radicale import config  # noqa: E402

from franklin_radicale.group_all_users import Group  # noqa: E402


def _cfg(group="franklin", excluded="calendar-publisher"):
    configuration = config.load()
    configuration.update({"group": {
        "type": "franklin_radicale.group_all_users",
        "franklin_group": group,
        "franklin_excluded_users": excluded,
    }}, "test", privileged=True)
    return configuration


def test_every_normal_user_is_in_the_group():
    g = Group(_cfg())
    assert g.groups("sal.cobian") == {"franklin"}
    assert g.groups("someone-new") == {"franklin"}


def test_publisher_is_excluded():
    # As a member it would get the shares mapped into its own home; it is the
    # one account that must see only the real collections it writes to.
    assert Group(_cfg()).groups("calendar-publisher") == set()


def test_anonymous_is_in_no_group():
    assert Group(_cfg()).groups("") == set()


def test_exclusion_list_is_parsed_with_whitespace_and_multiple_names():
    g = Group(_cfg(excluded=" calendar-publisher , svc-bot ,"))
    assert g.groups("svc-bot") == set()
    assert g.groups("calendar-publisher") == set()
    assert g.groups("sal.cobian") == {"franklin"}


def test_empty_group_name_is_refused():
    with pytest.raises(RuntimeError):
        Group(_cfg(group=""))


def test_app_consults_custom_group_plugins():
    """Guard the FRANKLIN PATCH in radicale/app/__init__.py.

    Upstream only called the group module for type "htgroup", so a custom
    plugin was loaded but never consulted on CalDAV requests and every
    share-by-group entry silently did nothing. If an upstream merge restores
    the htgroup-only condition, this fails.
    """
    src = open(os.path.join(HERE, "..", "..", "radicale", "app", "__init__.py")).read()
    assert re.search(r'if group_type not in \["none", "from_auth"\]:\s*\n\s*'
                     r'self\._rights\._user_groups = self\._group\.groups\(login\)', src), \
        "custom group plugins are not consulted on CalDAV requests"
    assert 'if group_type in ["htgroup"]:' not in src
