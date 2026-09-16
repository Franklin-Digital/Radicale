"""Radicale group plugin: every authenticated dashboard user is in one group.

WHY THIS EXISTS
---------------
Sal, 2026-09-16: "make the shared calendars show up automatically".

The Earnings and Economic Indicator calendars live under the
`calendar-publisher` principal. A CalDAV client discovers calendars under the
LOGGED-IN user's own calendar home (`/sal.cobian/`), so those calendars were
readable but never listed -- each had to be added by URL.

Radicale (>= 3.8.0) supports share-by-GROUP: one share entry whose path starts
with the placeholder `/{user}/` is materialised into the calendar home of every
member of a group, always enabled, never hidden, read-only for the member. The
missing piece is group membership, and the stock backends do not fit:

  * htgroup   -- a hand-maintained member list. A new dashboard user would not
                 see the calendars until someone edited a file.
  * from_auth -- only supported for ldap/pam auth.

Every authenticated user of this server IS a dashboard user (the auth plugin
reads the dashboard's `users` table), so membership needs no lookup at all.

THE EXCLUSION IS LOAD-BEARING
-----------------------------
`calendar-publisher` must NOT be a member. It owns the real collections and is
the one account that writes to them; as a member it would also receive the
shares mapped (read-only) into its own home. Keeping it out of the group keeps
its view of its own principal exactly what it was.

Excluded users are configured, not hardcoded:

    [group]
    type = franklin_radicale.group_all_users
    franklin_group = franklin
    franklin_excluded_users = calendar-publisher
"""

from typing import Set

from radicale import config, group
from radicale.log import logger


class Group(group.BaseGroup):

    def __init__(self, configuration: config.Configuration) -> None:
        super().__init__(configuration)
        self._group = configuration.get("group", "franklin_group")
        self._excluded = {
            u.strip()
            for u in configuration.get("group", "franklin_excluded_users").split(",")
            if u.strip()
        }
        if not self._group:
            # An empty group name would leave every share-by-group entry
            # unreachable while looking configured.
            raise RuntimeError("franklin group plugin: franklin_group is empty")
        logger.info("franklin group plugin: group %r, excluded %r",
                    self._group, sorted(self._excluded))

    def _groups(self, login: str) -> Set[str]:
        # Anonymous requests carry an empty login and belong to nothing.
        if not login or login in self._excluded:
            return set()
        return {self._group}
